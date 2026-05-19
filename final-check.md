# Final Check：混合召回 + 参考来源 + 早合并 + Mermaid 验收清单

> 涉及决策点：D-EK（混合召回）/ D-EL（参考来源）/ D-EM（早合并占位）/ D-EN（Mermaid PNG 验收）

---

## Phase A：BM25 + 向量混合召回

### A1. 选型与基础设施
- [ ] 确认 DashScope `text-embedding-v3` 在当前 API key 配额内可用，单次 25 条批量调用 RT < 2s
- [ ] 决策：向量不入 pgvector，按项目维度落 JSONB 到 `projects.blackboard_embeddings`
- [ ] 决策：融合算法用 RRF（k=60），不做归一化加权和
- [ ] settings 新增 `embedding_model` / `hybrid_retrieval_enabled` / `hybrid_rrf_k` 字段，且 `.env.example` 同步

### A2. 新增代码
- [ ] `services/embeddings.py:embed_texts` 完成，含 25 条批量、retry/backoff、失败回退全零向量并记 warning
- [ ] `services/hybrid_retrieval.py:rrf_fuse` 完成，纯函数好测
- [ ] 改造 `services/blackboard_retrieval.py:BlackboardIndex`：构造接收 embeddings，search 接收 query_embedding，无 embedding 时降级纯 BM25
- [ ] 改造 `workflow/nodes/categorize_blackboard.py`：分类完后一次性 embed 全部条目，写入 state
- [ ] 改造 `workflow/prompts/write_chapter_prompt.py:build_messages`：query embed 后传入 hybrid search
- [ ] 改造 `services/blackboard_retrieval.py:make_blackboard_tool_handler`：tool 路径也走混合召回

### A3. State 与持久化
- [ ] `workflow/state.py` 加 `blackboard_embeddings` 字段
- [ ] `CURRENT_WORKFLOW_SCHEMA_VERSION` 由 4 升到 5
- [ ] alembic 0011：`projects.blackboard_embeddings` JSONB 列，默认 null
- [ ] 老 checkpoint resume 测试：字段缺失时不报错，自动回落 BM25

### A4. 测试
- [ ] `test_rrf_fusion.py`：已知两组排名 → 验证融合分数公式
- [ ] `test_hybrid_retrieval.py`：构造 token 不重合但语义相近的 query，验证向量召回能补回 BM25 漏的条目
- [ ] `test_embeddings_fallback.py`：mock DashScope 报错，确认全零向量回退、warning 落日志
- [ ] 老 `test_blackboard_retrieval.py` 全绿（确认向后兼容）

---

## Phase B：参考来源采集与展示

### B1. 数据模型
- [ ] `models/chapter.py` 加 `references: list[dict]`（JSONB），默认 `[]`
- [ ] alembic 0012：`chapters.references` 列
- [ ] `ChapterDetailDTO` 增加 `references: list[ReferenceDTO]`

### B2. 采集
- [ ] `BlackboardIndex.search` 返回的 hit dict 多带 `retrieval_method` 字段（值：`bm25` / `vec` / `bm25+vec`）
- [ ] `build_messages` 改签名返回 `(messages, references)`
- [ ] `make_blackboard_tool_handler` 加 `collector` 入参，每次 tool 调用追加 hits（标 `retrieval_method="tool"`）
- [ ] `workflow/nodes/write_chapter.py`：合并首轮 references + collector 内容，按 content 哈希去重，写入 chapter

### B3. 前端展示
- [ ] `ChapterReviewPage.tsx` 加 `<ReferencesPanel>` 折叠组件，标题"本章参考的资料（N 条）"，默认收起
- [ ] 每条展示 `source_doc · section`，悬浮显示 content_preview
- [ ] 文案明确"LLM 看过的资料"，不要写"引用"
- [ ] 空 references 时不渲染折叠区，避免视觉噪声

### B4. 测试
- [ ] `test_references_collection.py`：build_messages 返回的 references 包含正确 source_doc / section
- [ ] `test_tool_collector.py`：tool 调用 N 次 → collector 累计 N 批
- [ ] 前端手测：随便点一章看到资料列表，悬浮看到预览

---

## Phase C：早合并 + 未生成章节占位

### C1. 状态机
- [ ] `workflow/state.py` 加 `_finalize_early: bool = False`
- [ ] `Chapter.status` 枚举加 `not_generated`
- [ ] 前端 status 标签映射加 `not_generated` → "未生成"

### C2. 后端流程
- [ ] 新 API `POST /api/projects/{project_id}/finalize-early`，校验当前 phase 必须是 `chapter_review`
- [ ] `workflow/nodes/update_state.py` 收到 `finalize_early` payload → 设 `state._finalize_early = True`
- [ ] `workflow/nodes/pick_chapter.py`：检测 `_finalize_early` → 把所有 pending/awaiting_review 章节标 `not_generated` → `current_index = len(chapters)` 跳 assemble
- [ ] `workflow/nodes/assemble.py`：`not_generated` 章节注入占位文字：
  ```
  ## {section} {title}
  > **（本章未生成）** 该章节在用户提前合并时尚未生成正文。
  ```

### C3. 前端
- [ ] `ChapterReviewPage.tsx` 顶部加按钮"完成评审，提前合并"
- [ ] confirm 弹窗文案：`还有 N 章未生成，提前合并会在文档里以"（本章未生成）"占位。确认继续？`
- [ ] 触发后跳转项目状态总览，等待 assemble 完成

### C4. 测试
- [ ] `test_pick_chapter_finalize_early.py`：`_finalize_early=True` 时未生成章节标 `not_generated` 并路由 assemble
- [ ] `test_assemble_with_not_generated.py`：占位文字出现在正确章节顺序位置
- [ ] `test_finalize_early_api.py`：endpoint 正常发任务；phase 不对时 400
- [ ] DOCX 手测：导出的 docx 里占位章节有标题、目录编号连贯

---

## Phase D：Mermaid → PNG（已在 0a2c741 / 42e8d76 落地，补验收）

### D1. 已实现确认
- [x] `services/docx_export.py:_render_mermaid()` 已存在
- [x] PNG 宽度 60%（`width=60%`）
- [x] 居中（`fig-align=center`）
- [x] mmdc 失败回退源码块
- [x] mmdc 不存在时返回原始 markdown 不阻塞

### D2. 缺失项（本次补）
- [ ] 新增 `tests/test_mermaid_render.py`：用 docker 容器内 mmdc 跑一段最小 flowchart，断言生成的 PNG 存在且 size > 0
- [ ] 新增 `tests/test_docx_mermaid_attributes.py`：断言生成的 markdown 里 image 行包含 `width=60%` 和 `fig-align=center`
- [ ] 新增 `tests/test_mermaid_fallback.py`：mock mmdc 返回非零退出码，断言原 mermaid 代码块被保留
- [ ] DOCX 手测：导出包含流程图的项目，确认图片不占满页宽、居中、视觉舒适
- [ ] 巡检 Mermaid 渲染失败率：worker 日志统计连续 3 天 `_render_mermaid` warning 占比，>5% 时回查 prompt

---

## Phase E：迁移与文档

### E1. Migration
- [ ] 0011（blackboard_embeddings）+ 0012（chapter.references）依次落地
- [ ] 本地空库 `alembic upgrade head` 全绿
- [ ] 服务器上 dry-run：`alembic upgrade head --sql` 检视 SQL 无破坏性

### E2. 文档
- [ ] README "工作流概览"补混合召回章节
- [ ] README "已知限制" 加两条：embedding 失败回退纯 BM25；早合并占位章节会让目录编号断号
- [ ] CLAUDE.md architecture 节加 D-EK / D-EL / D-EM / D-EN 标记
- [ ] IMPLEMENTATION_SPEC 加四条决策点条目

### E3. 部署脚本
- [ ] `scripts/upgrade-to-hybrid-retrieval.sh`：git pull gitee main → flush dry-run → rebuild + restart → alembic upgrade head → 三方校验
- [ ] 脚本预检：检测 DashScope embedding API 配额（轻量请求一次拿响应码）

---

## Phase F：上线后回归

- [ ] 跑一个完整新项目，确认 LLM-2 worker 日志里出现 `hybrid_retrieval_hit`（向量召回命中）
- [ ] 在 ChapterReviewPage 看到非空 references 列表
- [ ] 至少试一次提前合并，确认 docx 里占位文字位置正确
- [ ] 至少试一次包含 Mermaid 的章节，确认 docx 图片 60% 宽且居中
- [ ] 老项目 resume：从备份恢复一个 schema=4 的 checkpoint，确认能继续跑（降级 BM25）
- [ ] 灰度三天，无新增 P0 异常后清理 `hybrid_retrieval_enabled` 开关代码（可选，留作 escape hatch 也行）

---

## 提交切分

- [ ] Commit 1：Phase A（embedding + hybrid + 测试 + migration 0011）
- [ ] Commit 2：Phase B（references 采集 + DB 列 + API + 前端 + migration 0012）
- [ ] Commit 3：Phase C 后端（early-finalize + 占位符 + 测试）
- [ ] Commit 4：Phase C 前端（按钮 + 文案）
- [ ] Commit 5：Phase D 测试补齐（Mermaid 三测试）
- [ ] Commit 6：Phase E 文档 + 迁移脚本

---

## 风险与回退

- [ ] **DashScope embedding API 故障**：自动回退全零向量 = 退化纯 BM25，工作流不阻塞
- [ ] **Schema v5 旧 checkpoint**：`dict.get` + 默认值，老项目自动降级
- [ ] **早合并的 DOCX 目录断号**：业务可接受，文档明确说明
- [ ] **Mermaid PNG 60% 在小尺寸图上偏小**：先观察一周，必要时按图宽自适应（>800px 用 60%，否则 80%）
- [ ] **混合召回回退开关**：`BID_APP_HYBRID_RETRIEVAL_ENABLED=false` 可一键关闭，立即回 BM25
