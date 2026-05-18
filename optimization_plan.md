# 模版规范落地执行计划

> 基于 `rule.md` 与当前 bid-app 工作流的差距分析，列出 5 个阶段的详细执行计划。
> 阶段按依赖顺序排，每阶段独立 PR；阶段 1 是前置，阶段 2/3 可并行，阶段 4 在 1+2 完成后落，阶段 5 可与阶段 1 并行起步。

---

## 阶段 1：模版骨架预设（前置）

### 目标
把"模版骨架"从 LLM-1 prompt 里的暗示，固化成 **JSON 资产**；LLM-1 改为"在骨架上裁剪 + 充实叶子节点"。同时引入 `chapter_type` 字段，后续 2/3/4 都依赖它。

### 数据模型变更（⚠️ 不向后兼容）
- `WorkflowState.schema_version`：**3 → 4**
- `WorkflowState` 新增字段：
  ```python
  template_pack: str | None      # 模版包标识，例如 "gov_consumer_platform_v1"
  ```
- 每个 `chapters[i]` dict 新增字段（在 `parse_outline._normalize_chapter` 里 setdefault）：
  ```python
  chapter_type: str   # one of: module | principle | architecture | meeting
                      #          | image_only | table_only | normal
  template_slot: str  # 模版骨架里的稳定 ID（如 "perf_case_1.contract_amount_page"）
  required_anchors: list[str]  # 见阶段 2/3，类型相关的硬约束
  ```

### 涉及文件
- 新增 `backend/src/bid_app/workflow/templates/__init__.py`
- 新增 `backend/src/bid_app/workflow/templates/gov_consumer_platform_v1.json`（基于 rule.md §1-9 的固定骨架）
- 改 `backend/src/bid_app/workflow/prompts/outline_prompt.py`
- 改 `backend/src/bid_app/workflow/nodes/parse_outline.py`
- 改 `backend/src/bid_app/workflow/state.py`
- 改 `backend/src/bid_app/workflow/nodes/generate_outline.py`（在调 LLM-1 之前选骨架）
- 改 `backend/src/bid_app/workflow/nodes/material_understanding.py`（产出"项目类别"信号）
- 新增 alembic 迁移：`projects.template_pack TEXT NULL`
- 改 `IMPLEMENTATION_SPEC.md`：新增 **D-EF 模版骨架预设** 决策标签

### 实施步骤

1. **写骨架 JSON**：把 rule.md §1-9 完整转写成一棵树。叶子节点带 `chapter_type / template_slot / required_anchors / default_key_points`。一个粗略结构：

   ```jsonc
   {
     "id": "gov_consumer_platform_v1",
     "title": "政企信息化-票务消费类标书",
     "skeleton": [
       {"title": "评审索引表", "chapter_type": "table_only",
        "template_slot": "review_index", "fixed": true},
       {"title": "技术商务符合性评审细项", "chapter_type": "table_only",
        "template_slot": "compliance_index", "fixed": true},
       {"title": "项目建设及技术事项方案", "children": [
         {"title": "项目概述", "children": [
           {"title": "项目背景", "chapter_type": "normal", "target_pages": 1},
           {"title": "项目目标", "chapter_type": "normal", "target_pages": 1}
         ]},
         {"title": "系统整体架构", "children": [
           {"title": "总体设计原则", "chapter_type": "principle",
            "required_anchors": ["开放性","灵活性与扩展性","稳定性","易维护性","安全性"]},
           {"title": "应用架构方案", "chapter_type": "architecture",
            "required_anchors": ["接入层","网关层","业务服务层","能力中心层",
                                 "集成接口层","数据服务层","基础设施层"]}
         ]},
         {"title": "应用解决方案", "expandable": true,
          "child_chapter_type": "module"}   // LLM-1 在这里展开 N 个业务模块
       ]},
       // ...
       {"title": "技术/商务响应与偏离表", "chapter_type": "table_only",
        "template_slot": "deviation_table", "fixed": true}
     ]
   }
   ```

2. **material_understanding 加分类**：在现有 prompt 末尾追加一项 `project_category` 输出（候选枚举：`gov_consumer_platform / smart_city / financial_system / ...`），后续 fallback 到 `gov_consumer_platform_v1`。

3. **generate_outline 改路由**：调 LLM-1 之前根据 `material_understanding.project_category` 选骨架；把骨架作为 prompt **强制结构**注入（"你必须生成符合下列骨架的 toc，不可增删 fixed 节点，可在 expandable 节点下展开 3-7 个 module"）。

4. **parse_outline 强制校验**：展平后比对骨架的 `fixed` 节点是否齐全；缺失 → 抛 `OutlineSkeletonViolation`，generate_outline 重试 1 次后才进入用户审核。

5. **schema bump**：跑 `flush_running_workflows.py` 清退在跑项目，发布说明 v3 → v4 风格的迁移通告。

### 验收标准
- 用同一份招标材料连续生成 3 次目录，**9 个 H1 出现率 100%**、顺序一致。
- 业绩章、资质章的叶子全部带 `chapter_type=image_only`。
- 偏离表叶子带 `chapter_type=table_only`。
- 单元测试：`tests/workflow/test_template_skeleton.py` 覆盖"骨架解析 / fixed 节点缺失检测 / expandable 节点展开"。

### 风险 & 回滚
- 风险：用户的招标材料和骨架领域差距太大 → 加 fallback 包 `generic_v1`（只保留"评审索引/偏离表/业绩/资质/人员"5 个共性章）。
- 回滚：env `BID_APP_TEMPLATE_PACK_DISABLED=1` 跳过骨架注入，LLM-1 退回完全自由。

---

## 阶段 2：按 chapter_type 分流的章节生成

### 目标
write_chapter 不再是单一 prompt + 一刀切的"自由发挥"，按 `chapter_type` 路由到 5 套生成策略，其中 2 个类型完全绕过 LLM-2。

### 涉及文件
- 改 `backend/src/bid_app/workflow/nodes/write_chapter.py`
- 改 `backend/src/bid_app/workflow/nodes/chapter_generate_gate.py`（增加 image_only/table_only 短路）
- 新增 `backend/src/bid_app/workflow/prompts/write_module_prompt.py`
- 新增 `backend/src/bid_app/workflow/prompts/write_principle_prompt.py`
- 新增 `backend/src/bid_app/workflow/prompts/write_architecture_prompt.py`
- 新增 `backend/src/bid_app/workflow/prompts/write_meeting_prompt.py`
- 现有 `write_chapter_prompt.py` 保留为 `normal` 类型的回退
- 新增 `backend/src/bid_app/workflow/renderers/image_only.py`（直接渲染 H3 标题 + 图位）
- 新增 `backend/src/bid_app/workflow/renderers/table_only.py`（渲染评审表/偏离表表头骨架）

### 实施步骤

1. **5 套 prompt 的差异化清单**（每套 200 行内）：

   | type | system prompt 核心约束 |
   |---|---|
   | `module` | 三段式锚点 `技术实现：/ 关键适配：/ 典型业务流程：`；每个流程必含 `流程目标 / 处理步骤 / 关键控制点` 三要素；流程末必有一行 `对应时序图：<流程名>`；**禁表格、禁代码块、禁引用块**；段长 ≤ 180 字 |
   | `principle` | 输出**恰好 N 条**（N 从 `required_anchors` 来），格式 `<编号>、<名称>：\n<100–180 字描述>`；不许多写少写 |
   | `architecture` | 必须明确出现 `required_anchors` 里的全部关键词；末尾必出一段"以下为系统整体架构图，参见对应架构图：总体架构"锚点 |
   | `meeting` | 四要素 `会议目标 / 日期与时间 / 参加人员 / 主要议程及责任` |
   | `normal` | 现有 prompt 保留 |

2. **write_chapter.run 路由**：

   ```python
   chapter_type = chapter.get("chapter_type", "normal")
   if chapter_type in ("image_only", "table_only"):
       # 短路：调用 renderers，不调 LLM-2
       text = render_template_slot(chapter, project_state)
       return {"_pending_chapter_text": text}
   build_fn = _PROMPT_BUILDERS[chapter_type]
   messages = build_fn(chapter=chapter, ...)
   ```

3. **image_only 渲染器**：根据 `template_slot` 渲染固定 H3 列表（业绩章 4 项扫描件标题、资质章 4 项体系标题），实际图片由用户在前端手动上传到对应 slot；assemble 阶段拼图。

4. **table_only 渲染器**：偏离表/评审表，表头按 rule.md §1 §9 固定列名；表体由 `material_understanding` 抽出的"评分项 / 商务条款"自动填充（暂不覆盖到的留空）。

5. **chapter_generate_gate 调整**：识别 image_only/table_only → 跳过"是否值得生成"判断，直接放行到 write_chapter 走 renderer。

### 验收标准
- 一个含 7 个业务模块章的工程，所有 module 章末段都符合"流程末尾有 `对应时序图：xxx` 行"；正则 `r"对应时序图：(.+)"` 命中数 == 流程数。
- `principle` 章输出严格 5 条且名称等于 `required_anchors`。
- 业绩章、偏离表章生成耗时 < 1 秒（因为不调 LLM-2），LLM token 用量为 0。
- 测试：`tests/workflow/test_chapter_routing.py` 覆盖每种 `chapter_type` 的 prompt 选择 / renderer 输出。

### 风险 & 回滚
- 风险：5 套 prompt 维护成本。**对策**：把"占位符规则 / 中英文空格 / Mermaid 围栏"等共用部分抽到 `_LLM2_COMMON_SUFFIX` 常量，5 套 prompt 拼接。
- 回滚：每种类型加 env 开关 `BID_APP_PROMPT_<TYPE>_DISABLED=1`，命中后 fallback 到 `normal` prompt。

---

## 阶段 3：锚点驱动的可视化生成（与阶段 2 并行）

### 目标
gen_visuals 从"自由发现 0–4 处"改为"扫描锚点 → 每个锚点强制生成 1 张图"。

### 涉及文件
- 改 `backend/src/bid_app/workflow/nodes/gen_visuals.py`
- 改 `backend/src/bid_app/workflow/prompts/review_chapter_prompt.py`（拆成 2 个 builder）
- 改 `backend/src/bid_app/workflow/nodes/merge_chapter.py`（锚点匹配优先级）

### 实施步骤

1. **正文锚点正则**：

   ```python
   _SEQ_ANCHOR_RE = re.compile(r"^对应时序图：(.+?)$", re.MULTILINE)
   _ARCH_ANCHOR_RE = re.compile(r"^对应架构图：(.+?)$", re.MULTILINE)
   ```

2. **gen_visuals.run 改写**：

   ```python
   anchors = _scan_anchors(chapter_text)
   # anchors = [{"type":"sequence", "name":"商户登录与权限装载流程"}, ...]
   tasks = [_gen_one_visual(a, chapter_text, ...) for a in anchors]
   items = await asyncio.gather(*tasks, return_exceptions=True)
   ```

   每个 anchor 调一次 LLM-3，prompt 收缩到"给定流程名 + 全文上下文 → 输出一个 sequenceDiagram"，单次 max_tokens 1024。

3. **LLM-3 拆 2 个 builder**：
   - `build_sequence_messages(flow_name, chapter_body)` — 输出单张 sequenceDiagram
   - `build_architecture_messages(layers, chapter_body)` — 输出单张 flowchart TD（七层架构图）

4. **merge_chapter 锚点优先**：现有 `_insert_visual_blocks` 改为：
   - 第一遍：按 `对应时序图：(.+)` 行精确匹配 → 图块插在该行之后
   - 第二遍（兼容旧）：按 anchor 字符串匹配（保留兜底）

5. **失败兜底**：单个 anchor LLM-3 失败 → 留一行 `> ⚠️ <流程名> 时序图渲染失败，请人工补图`，不影响其他图。

### 验收标准
- 模块章 5 个流程 → 生成 5 张图，**1:1**。
- 总体架构章（`chapter_type=architecture`）→ 生成且仅生成 1 张七层图。
- 业绩章、资质章（`image_only`）→ gen_visuals 短路返回 `{"items":[]}`。
- 单张图调用 token 减半（短上下文），并发 5 个 anchor 总耗时 ≈ 单次调用。
- 测试：`tests/workflow/test_visual_anchors.py` 覆盖"3 个流程 → 3 个 sequenceDiagram / 0 流程 → 0 图 / 失败兜底"。

### 风险 & 回滚
- 风险：并发 LLM-3 撞限流。**对策**：用 `asyncio.Semaphore(3)` 限并发；超 3 个 anchor 时分批。
- 回滚：env `BID_APP_VISUAL_ANCHOR_MODE=0` 回旧自由模式。

---

## 阶段 4：merge_chapter 后的结构化校验器

### 目标
新增确定性校验，命中失败时**自动**喂 `revision_feedback` 让 LLM-2 重写一次，仍失败再进 `human_review`。把人审 retry 率打下来。

### 涉及文件
- 新增 `backend/src/bid_app/services/template_validator.py`
- 改 `backend/src/bid_app/workflow/nodes/merge_chapter.py`（末尾调校验）
- 改 `backend/src/bid_app/workflow/nodes/update_state.py`（增加 `auto_revise` 分支）
- 改 `backend/src/bid_app/workflow/graph.py`（merge_chapter → 条件边：通过 → human_review；失败 + 自动重试额度 → write_chapter）
- 新增 alembic 迁移：`chapter_versions.validation_report JSONB NULL`
- 改 `frontend/src/pages/ChapterReviewPage.tsx`：展示校验红绿点

### 实施步骤

1. **校验器接口**：

   ```python
   @dataclass
   class ValidationIssue:
       code: str        # missing_anchor / wrong_principle_count / forbidden_marker / ...
       severity: str    # error | warn
       message: str
       hint: str        # 给 LLM-2 的 revise feedback 文本

   def validate_chapter(text: str, chapter: dict) -> list[ValidationIssue]:
       checks = _CHECKS_BY_TYPE[chapter["chapter_type"]]
       return [issue for check in checks for issue in check(text, chapter)]
   ```

2. **规则集**（每个 ≤ 30 行）：

   | 规则 | 适用 chapter_type | 检测 |
   |---|---|---|
   | `module_three_section` | module | 文中是否同时出现 `技术实现：`、`关键适配：`、`典型业务流程：` |
   | `flow_three_elements` | module | 每个 `对应时序图：` 行之前的段落是否含 `流程目标 / 处理步骤 / 关键控制点` 三关键词 |
   | `principle_count_and_names` | principle | 编号条数 == `len(required_anchors)`，且每条标题命中白名单 |
   | `architecture_layers_complete` | architecture | `required_anchors` 关键词 100% 命中 |
   | `image_only_h3_complete` | image_only | H3 数 == `template_slot` 配置里的 expected_h3 数 |
   | `deviation_table_complete` | table_only / deviation | 三条说明 + 落款两行齐全 |
   | `forbidden_list_markers` | 全部 | 无 `①②③/◆▶●/一、二、` |
   | `paragraph_length_p90` | module/normal | 段落 p90 长度 ≤ 180 字（软警告） |

3. **merge_chapter 集成**：

   ```python
   issues = validate_chapter(full_chapter, chapter)
   await _save_validation_report(run_id, current, issues)
   has_error = any(i.severity == "error" for i in issues)
   return {
       "_pending_chapter_text": full_chapter,
       "_validation_issues": [asdict(i) for i in issues],
       "_should_auto_revise": has_error and retry_count < max_auto_retries,
   }
   ```

4. **graph 条件边**：merge_chapter → 路由器：
   - `_should_auto_revise` → 走 `update_state` 的 `auto_revise` 分支，拼接 `hint` 作为 `revision_feedback`，retry_count++，回到 `write_chapter`
   - 否则 → `human_review`（带上 issues 给前端显示）

5. **`max_auto_retries`**：从 `Project.settings` 读，默认 1（最多自动重试 1 轮，避免死循环）。

6. **前端**：在 ChapterReviewPage 顶部加一行红绿点 chip："✓ 三段式 / ✓ 流程三要素 / × 段落长度（3 段超过 180 字）"，hover 看详情。

### 验收标准
- 故意 LLM-2 漏写 `典型业务流程：` → 校验器 1 次自动 revise 成功率 ≥ 70%（手测 20 章统计）。
- 业绩章漏一个 H3 → 校验器直接报红 + 前端高亮缺失项。
- 校验本身耗时 < 50ms。
- 测试：`tests/services/test_template_validator.py` 覆盖每条规则的正反例。

### 风险 & 回滚
- 风险：自动 revise 引起死循环。**对策**：硬上限 `max_auto_retries=1`，且 LLM-2 prompt 里把 `hint` 拼到现有 `REVISION_TEMPLATE` 的 `revision_feedback` 段，复用既有重试通路。
- 风险：校验过严误伤合理输出。**对策**：所有规则按 severity 分级，先把高置信度的标 error，疑似的标 warn 不触发自动重试，跑 1 周观察误报率后调级。
- 回滚：`BID_APP_VALIDATOR_AUTO_REVISE=0` 关掉自动重试，只做信息展示。

---

## 阶段 5：文体黑名单（最低 ROI，最快落地）

### 目标
把 rule.md §10-11 的"措辞 / 列表符号"规则固化进 LLM-2 system prompt **和**校验器。

### 涉及文件
- 改 `backend/src/bid_app/workflow/prompts/_common.py`（新建，所有 5 套 prompt 共用尾巴）
- 改 `backend/src/bid_app/workflow/postprocess.py`（已有，加一个 normalize 步骤）
- `template_validator.py` 里复用 `forbidden_list_markers` 规则

### 实施步骤

1. **新增共用 system prompt 尾巴**（注入到所有 LLM-2 prompt）：

   ```
   ## 文体硬规范（违反即被自动拒收重写）
   1. 列表编号白名单：顶层用 `1. xxx` 或 `1、xxx`；二级用 `（1）xxx`。
      禁止使用：`一、二、` / `①②③` / `◆▶●■` / `✓✗` / 任何 emoji。
   2. 段长 ≤ 180 字；超长用 ### 子节或编号列表拆。
   3. 不写感叹号、营销口号、未量化的形容词（"非常优秀""极大提升"）。
   4. 中英文 / 数字混排时数字前后加半角空格：`15 分钟` 不写 `15分钟`。
   5. 引号统一用中文双引号 ""，不用 ASCII '"。
   ```

2. **postprocess 加一步规范化**：
   - 全角数字 → 半角
   - 行首符 `①②③/◆▶●` → 改为 `1./2./3.`（保守替换，不动行尾）
   - 中英文混排自动加半角空格（用现成的 pangu 风格逻辑，纯正则实现 30 行）

3. **校验器加 `forbidden_list_markers`**：postprocess 兜底之后再扫一遍，命中即 warn 级（让 LLM-2 下次写更注意），命中数 > 3 升为 error。

### 验收标准
- 抽 20 个章节，输出里 `①②/一、二、` 出现总数 = 0。
- 中英数空格命中率 > 95%（脚本统计）。
- 测试：`tests/workflow/test_postprocess_style.py` 覆盖每条规则的正反例 + 幂等性。

### 风险 & 回滚
- 风险：postprocess 误伤原文专有名词（如包含 ① 的产品名）。**对策**：维护白名单（极少见）；保守起见只动行首符号、不动行内。
- 回滚：`BID_APP_STYLE_NORMALIZE=0` 跳过 postprocess 规范化步骤。

---

## 全局工程安排

### 排期与并行度

```
Week 1
  ├─ 1 模版骨架（前置，必须先完成）
  └─ 5 文体黑名单（独立，可与 1 并行）

Week 2
  ├─ 2 按类型分流 prompt（依赖 1 的 chapter_type 字段）
  └─ 3 锚点驱动可视化（依赖 2 的 module prompt 形态）

Week 3
  └─ 4 结构化校验器（依赖 1 + 2 完成）
```

### 一次性 Spec / 迁移变更（合并到阶段 1 PR）
- `IMPLEMENTATION_SPEC.md` 新增 §25 模版规范，标签 **D-EF**（模版骨架）/**D-EG**（章节类型分流）/**D-EH**（锚点驱动可视化）/**D-EI**（结构化校验）/**D-EJ**（文体规范化）
- `state.py:CURRENT_WORKFLOW_SCHEMA_VERSION` 3 → 4
- 新增 alembic 迁移：`projects.template_pack TEXT NULL` + `chapter_versions.validation_report JSONB NULL`
- 发布前执行 `scripts/flush_running_workflows.py`（v3 → v4 不向后兼容）

### 测试策略
- 每阶段一个独立 PR（不要打包，便于 review）。
- 每个阶段必带：单元测试（覆盖 prompt builder / validator / renderer）+ 一个端到端冒烟（CLI 模式跑通 1 个完整工作流）。
- 验收用同一份 rule.md 来源的招标材料做"金本位"对比，每个阶段后输出 diff 报告。

### 量化目标
| 阶段 | 指标 | 当前 | 目标 |
|---|---|---|---|
| 1 + 2 | 目录骨架命中率 | 40–60% | 100% |
| 3 | 流程图数量误差 | 漏 ≈ 30% | 0 |
| 4 | 人工 revise 触发率 | ≈ 40% | < 20% |
| 5 | 黑名单符号出现率 | 偶发 | 0 |

---

## 附：核心代码挂点速查

| 挂点 | 文件 | 当前行号 | 阶段 |
|---|---|---|---|
| `WorkflowState` schema bump | `workflow/state.py:17` | — | 1 |
| 章节 dict 字段初始化 | `workflow/nodes/parse_outline.py:37-67` | — | 1 |
| LLM-1 prompt 构造 | `workflow/prompts/outline_prompt.py:132-218` | — | 1 |
| 项目类别识别 | `workflow/prompts/material_understanding.py` | — | 1 |
| write_chapter 路由 | `workflow/nodes/write_chapter.py:295-380` | — | 2 |
| LLM-2 prompt 路由 | `workflow/prompts/write_chapter_prompt.py:280` | — | 2 |
| gen_visuals 锚点扫描 | `workflow/nodes/gen_visuals.py:101-148` | — | 3 |
| merge_chapter 视觉块插入 | `workflow/nodes/merge_chapter.py:80-145` | — | 3 |
| 工作流条件边 | `workflow/graph.py` | — | 4 |
| postprocess 规范化 | `workflow/postprocess.py` | — | 5 |
