# bid-app v2 PR-级 Checklist

> 基于 2026-05-13 拍板的 7 项优化方向 + 6 条关键决策（D1–D6），按 PR 维度拆解。
> 每个 PR 含：范围、改动文件、新增/迁移、测试、验收点、依赖。
> 总体顺序：**UI-1 → M6 → M7 → M8 → M9**，UI-Track 与功能 PR 错位并行。

---

## PR 进度（每完成一个 PR check 一次,然后 commit + push）

- [x] PR-UI-1 design tokens 基线
- [x] PR-M6-1 脱敏服务 (D3)
- [x] PR-M6-2 单章 Word 导出
- [x] PR-M7-1 schema 迁移 + flush CLI
- [x] PR-M7-2 解除上传限制 (D5)
- [x] PR-M7-3 HTML 黑板 + 备份脚本 (D2)
- [ ] PR-UI-2 现有页面 retrofitting
- [ ] PR-M8-1 材料理解先行
- [ ] PR-M8-2 目录交互编辑
- [ ] PR-M9-1 选择性生成 + 增量补齐

---

## 顶层共识（每个 PR 都要遵守）

- **D1 断旧续新**：所有 `WorkflowState` 改动一次性合到 PR-M7-1；上线 checklist 含 `flush_running_workflows` CLI。
- **D2 黑板 = 磁盘 + DB 路径**：写盘走 tmp → fsync → atomic rename → DB commit；备份脚本同步覆盖 `/var/lib/bid-app/projects/`。
- **D3 脱敏不可逆**：占位符进章节正文，UI 全链路提示「占位符未替换」；黑板存原文，仅在 `services/llm.py` 出栈时替换。
- **D4 未选章节跳过**：`assemble` 不补占位符；编号保留原序号。
- **D5 上传上限**：单文件 200MB、项目总 500MB。
- **D6 UI = 瑞典编辑风**：大留白 / 强层级 / 衬线大标题 + 无衬线正文 / 单色 + 1 个克制 accent / 1px border 替代阴影。
- **跨切**：每个 PR 都要更新 `IMPLEMENTATION_SPEC.md`（追加 `D-EF` 起的决策号）+ `REQUIREMENTS.md` 对应 FR / NFR。

---

## UI-Track（瑞典编辑风 design system，贯穿）

### PR-UI-1：design tokens 基线（先于 M6 合并）

**范围**：搭出新的 design system 基础层，不动现有页面逻辑；后续所有功能 PR 用新 tokens。

**改动文件**：
- `app/frontend/tailwind.config.ts` —— 重写 `theme.extend.colors` / `fontFamily` / `fontSize` / `spacing`
- `app/frontend/src/styles/globals.css` —— CSS 变量定义 + 基础排版规则
- `app/frontend/components.json` —— shadcn theme 切换到新 tokens
- `app/frontend/src/components/ui/*.tsx` —— 改 Button / Card / Input / Dialog / Tabs / Toast 的 variant 默认值
- `app/frontend/index.html` —— `<link>` 引入字体（Söhne / Tiempos 或 Noto Serif SC + Inter 的开源替代）

**Design tokens 规格**：

```css
/* Color (monochrome + 1 accent) */
--ink: #111111;           /* 正文 */
--paper: #FAFAF7;         /* 背景，略带暖偏离纯白 */
--paper-2: #F1EFE8;       /* 次级背景，类似新闻纸 */
--rule: #1C1C1C1A;        /* 1px 分隔线 */
--mute: #6B6B66;          /* 次要文字 */
--accent: #B5471F;        /* muted rust，唯一强调色 */
--warn: #8B5A00;          /* 占位符提示 / 脱敏警告（D3 用） */

/* Typography */
--font-display: "Tiempos Headline", "Noto Serif SC", Georgia, serif;
--font-sans: "Söhne", "Inter", -apple-system, system-ui, sans-serif;
--font-mono: "Söhne Mono", "JetBrains Mono", monospace;

/* Type scale (editorial, 大跨度) */
--text-hero: 64px / 1.05 / -0.02em;   /* 单页大标题，serif */
--text-h1:   40px / 1.1  / -0.01em;
--text-h2:   28px / 1.2  / -0.005em;
--text-h3:   20px / 1.3  / 0;
--text-body: 16px / 1.6  / 0;
--text-meta: 13px / 1.4  / 0.02em;    /* 大写字间距，标签/日期 */

/* Spacing & layout */
--gutter: 24px;
--prose-max: 68ch;        /* 正文最大宽度，编辑感 */
--rhythm: 8px;            /* 基础垂直节奏 */

/* Borders & shadows */
border: 1px solid var(--rule);   /* 默认 */
box-shadow: none;                 /* 默认无阴影；少数浮层用极淡 */
```

**Component 风格变更**：
- `Button`：无圆角或 2px 圆角；default = ink 填充 + paper 文字；secondary = 1px ink border + transparent；ghost = underline 文本按钮
- `Card`：取消 shadow，改 1px border + paper-2 背景；header 用 serif；meta 用大写字间距 letter-spacing
- `Input`：无 border，仅底部 1px line；focus 时 line 加粗到 2px + 颜色变 accent
- `Dialog`：无圆角、无阴影；左侧 8px accent 色条作为视觉锚点
- `Tabs`：底部 indicator 1px line，active 加粗到 3px；不用胶囊背景
- `Toast`：极简，纯文本 + 顶部 1px accent line

**验收**：
- Storybook 或临时 `/dev/tokens` 路由可视化所有 token + 全部 component variant
- 现有 10 个页面运行无视觉破坏（旧页面允许暂时混搭，不算回归）
- `pnpm lint` `pnpm build` 通过

**依赖**：无（这是基线）

---

### PR-UI-2：现有页面 retrofitting（M6 之后并行）

**范围**：把现有 10 个页面（LoginPage / ProjectListPage / NewProjectPage / DocumentUploadPage / OutlineConfirmPage / ChapterReviewPage / ProposalPage / SettingsPage / AdminPage / ChangePasswordPage）改造到新 tokens。

**触达**：`app/frontend/src/pages/*.tsx` 全部。

**关键调整**：
- 每页顶部用 `<header>` 加 serif hero 标题 + 大写字间距的 meta（项目状态 / 日期）
- 列表页改成编辑感栅格：左侧大数字（章节编号 / 项目序号）+ 中间标题 serif + 右侧 meta
- 正文页 `max-w-prose` + 垂直 rhythm
- 表单页留白翻倍

**验收**：每页截图对比 before/after 贴 PR description；不动业务逻辑（diff 限定为 className / 结构）

**依赖**：PR-UI-1

---

### PR-UI-3 ~ UI-N：随 M7-M9 新页面同步交付

每个新前端页面（`MaterialUnderstandingPage` / 重写的 `OutlineConfirmPage` / 增量生成入口等）落地时**直接用新 tokens**，不允许走旧风格。

---

## M6 — 安全 & 快赢

### PR-M6-1：脱敏服务（项 7，D3 不可逆版）

**范围**：所有 LLM 调用前在出栈点替换敏感信息为占位符，不持久化映射。

**新增文件**：
- `app/backend/src/bid_app/services/redaction.py`
  - `redact(text: str, ctx: RedactionContext) -> str`
  - `RedactionContext`：request-scoped 内同名值 → 同占位符（确定性哈希 `sha1(value)[:6]` + 类型前缀，不存原值）
- `app/backend/tests/test_redaction.py`：覆盖嵌套 / 重叠 / 同名一致性 / 黑板未脱敏 / prompt 已脱敏 / allowlist 跳过 / 空字符串
- `app/backend/src/bid_app/services/redaction_rules.yaml`（项目级字典默认值，可被 `BID_APP_REDACTION_DICT_PATH` 覆盖）

**改动文件**：
- `app/backend/src/bid_app/services/llm.py`：三处 LiteLLM 调用统一前置脱敏
- `app/backend/src/bid_app/workflow/prompts/*.py`：prompt 末尾追加一句「文中形如 `__XXX_NNN__` 的标记是占位符，不必复述也不要替换为具体名称」
- `app/.env.example`：补 `BID_APP_REDACTION_DICT_PATH`
- `app/backend/src/bid_app/config.py`：新增 setting，pydantic 校验

**规则覆盖**：
- 正则：手机（11 位）/ 邮箱 / 身份证（18 位）/ 项目编号 `[A-Z]{2,}-?\d{4,}`
- 字典：公司后缀（公司 / 集团 / 院 / 局 / 中心 / 研究所）+ 项目级 allowlist
- 占位符格式：`__ORG_xxx__` `__PROJ_xxx__` `__PERSON_xxx__` `__PHONE_xxx__` `__EMAIL_xxx__` `__IDCARD_xxx__`（xxx = 6 位哈希）

**验收**：
- 调试日志里看不到任何原始公司名 / 项目号 / 手机号 / 邮箱（grep 抽样）
- 同一 request 内重复出现的「中铁某局」始终映射到同一占位符
- 黑板文件内仍是原文（`cat /var/lib/bid-app/projects/{id}/blackboard.html` 含原值）
- `pytest tests/test_redaction.py` 全绿

**前端配套**：
- `ChapterReviewPage` / `ProposalPage` 渲染章节正文时识别 `__XXX_NNN__` 模式，高亮显示 + 顶部 banner「⚠️ 本文档含占位符，导出前请手动替换」
- 提供「占位符清单」抽屉：列出本项目所有占位符 + 提示用户原值是什么类型（不展示原值）

**依赖**：无

---

### PR-M6-2：单章 Word 导出（项 6）

**范围**：每个 `pass` 状态章节支持单独导出 .docx。

**Schema 迁移**：alembic `add_docxjob_scope_chapter.py`
- `DocxJob.scope: ENUM('project', 'chapter')` 默认 `project`
- `DocxJob.chapter_id: UUID` 外键 → `chapters.id` (nullable)

**改动文件**：
- `app/backend/src/bid_app/api/docx.py` 或新建 `app/backend/src/bid_app/api/chapters.py` 中扩展：
  - `POST /chapters/{chapter_id}/export.docx`（鉴权：项目成员）
- `app/backend/src/bid_app/services/docx_export.py`：
  - 抽出 `_run_pandoc_pipeline(markdown, output_path)` 共用函数
  - 新增 `export_single_chapter(chapter_id) -> Path`，复用**全局串行锁**（D-CV 不动）
- `app/backend/src/bid_app/worker/tasks.py`：`generate_docx_task` 支持 `scope=chapter`，仍 `max_tries=1`（D-AY）
- `app/backend/src/bid_app/models/docx_job.py`：补 scope/chapter_id 字段 + 校验

**前端**：
- `app/frontend/src/pages/ChapterReviewPage.tsx`：章节 status=pass 时显示「导出本章」按钮
- 复用现有 SSE 进度组件
- 文件名：`{项目名}_{章节标题_safe}_{YYYYMMDD}.docx`

**验收**：
- 5 个单章导出并发触发 → 依次完成无崩溃（串行锁有效）
- 单章 docx 内 Mermaid 图正确渲染、占位符（D3）保留
- 跨章节并发：1 个整本导出 + 3 个单章导出依次排队
- `pytest` 新增 `test_docx_export_chapter_scope.py`

**依赖**：PR-M6-1（避免后续 retrofit 占位符提示）

---

## M7 — 文档摄入重构（最大一刀）

### PR-M7-1：一次性 schema 迁移 + flush CLI（先合，阻塞后续 M7-M9）

**范围**：把后续所有 milestone 用到的字段一次性迁完；提供清退残留项目的 CLI。

**新增 alembic migration**：`vN_v2_schema_bump.py`
```
ALTER TABLE documents
  ALTER COLUMN kind DROP NOT NULL,
  ADD COLUMN tags TEXT[],
  ADD COLUMN structured_html TEXT,
  ADD COLUMN byte_size BIGINT,
  ADD COLUMN mime_type TEXT;

ALTER TABLE projects
  ADD COLUMN blackboard_path TEXT;

-- WorkflowState 字段（存在 langgraph checkpoint 的 JSONB 里，
-- 无需 alembic；但要在 workflow/state.py 里加字段并升 schema_version）

-- DocxJob 已在 PR-M6-2 完成
```

**改动文件**：
- `app/backend/migrations/versions/vN_v2_schema_bump.py`
- `app/backend/src/bid_app/workflow/state.py`：
  - 新增 `schema_version: int = 2`（默认 2，老 checkpoint 反序列化时若 `schema_version != 2` 直接抛 `WorkflowSchemaMismatch`）
  - 新增字段：`material_understanding: str | None`, `outline_json: list[OutlineNode] | None`, `selected_chapter_ids: list[str] | None`
- `app/backend/src/bid_app/workflow/graph.py`：load checkpoint 时 catch `WorkflowSchemaMismatch` → 标记 project status=`aborted_schema_v1`
- `app/backend/src/bid_app/cli/flush_running_workflows.py` 新建：
  - `python -m bid_app.cli.flush_running_workflows --confirm`
  - 把所有 status in (`running`, `awaiting_review`) 的 project 标记为 `aborted_v1`
- `app/backend/src/bid_app/models/project.py`：扩展 status 枚举

**测试**：
- `tests/test_schema_migration.py`：起一个 v1 checkpoint → load → 期望 `WorkflowSchemaMismatch`
- 在测试库跑 `alembic upgrade head` → `alembic downgrade -1` → `upgrade head` 全通

**运维 checklist（写到 `app/scripts/v2-upgrade-runbook.md`）**：
1. `docker compose exec app /usr/local/bin/pg-backup.sh`（含 projects 目录，配合 PR-M7-3）
2. 全局横幅公告 24h 维护窗
3. `git pull && ./scripts/restart-after-update.sh`
4. `docker compose exec app python -m bid_app.cli.flush_running_workflows --confirm`
5. 用户通知：「v2 上线，旧项目请重建」

**依赖**：PR-M6-2（DocxJob.scope 已就位）

---

### PR-M7-2：解除上传限制（项 2 + D5）

**范围**：放开三选一文档校验、改单上传列表；单文件 200MB、总 500MB。

**改动文件**：
- `app/backend/src/bid_app/api/projects.py` 上传 endpoint：
  - 去除 `kind in ('tech_spec', 'scoring_rules', 'template')` 必填校验
  - 单文件 `len(content) > 200 * 1024 * 1024` → 422
  - 项目总和 `sum(documents.byte_size) > 500 * 1024 * 1024` → 422
  - 异步抽取：立即返回 202 + `document_id`，arq enqueue `extract_document_task`
- `app/backend/src/bid_app/config.py`：
  - `MAX_FILE_UPLOAD_BYTES = 200 * 1024 * 1024`
  - `MAX_PROJECT_UPLOAD_BYTES = 500 * 1024 * 1024`
- `app/backend/src/bid_app/worker/tasks.py`：新增 `extract_document_task`（max_tries=1）
- `app/backend/src/bid_app/services/document_extractor.py`：支持流式抽取大文件，写入 `Document.structured_html`
- `app/frontend/src/pages/DocumentUploadPage.tsx`：改单上传列表（按 UI-Track tokens）+ 可选 tag 输入框 + 单文件 / 总进度

**新增**：
- `app/frontend/src/components/UploadList.tsx`：编辑感列表，左侧序号、中间文件名 serif、右侧状态 meta
- `app/backend/src/bid_app/services/extraction_status.py`：抽取进度查询服务

**验收**：
- 上传 1 个 199MB 文件 → 成功；201MB → 422
- 上传到项目总和 = 499MB 时下一文件触发 422
- 上传 5 个 100MB 文件 → 全部 202 返回后异步完成；前端轮询/SSE 看到进度
- 删 `Document` 后总和正确回退

**依赖**：PR-M7-1

---

### PR-M7-3：HTML 黑板 + 备份脚本（项 4 + D2）

**范围**：抽取后产物落盘 + DB 路径；备份/恢复脚本同步覆盖 `projects/` 目录。

**新增文件**：
- `app/backend/src/bid_app/workflow/blackboard.py`
  - `write_blackboard(project_id, html) -> Path`：tmp → fsync → `os.replace` → 写 DB → commit；失败清理 tmp
  - `read_blackboard(project_id) -> str`：从路径读，缺失抛 `BlackboardMissing`（D-AY 不重试）
  - `delete_blackboard(project_id)`：项目删除时调
- `app/backend/src/bid_app/services/html_sanitize.py`：`bleach` 白名单（h1-h6 / p / table / ul / ol / li / code / pre / strong / em / a），去 inline style/script

**改动文件**：
- `app/backend/src/bid_app/workflow/nodes/extract_documents.py`：抽取完成后聚合 `Document.structured_html` → 清洗 → `write_blackboard`
- `app/backend/src/bid_app/workflow/prompts/*.py`：所有节点 prompt 增加 `{{ blackboard_excerpt }}` 变量（MVP 全文注入到 context 上限，超长截断到最相关段）
- `app/backend/src/bid_app/workflow/nodes/generate_outline.py` / `write_chapter.py` / `gen_visuals.py`：注入 blackboard_excerpt
- `app/backend/src/bid_app/api/projects.py` delete 路径：级联调 `delete_blackboard`
- `app/backend/src/bid_app/models/project.py`：SQLAlchemy event listener 兜底删盘（防止 API 路径漏调）

**运维脚本**：
- `app/docker/pg-backup.sh`：`pg_dump` 后追加 `tar -czf /backups/projects_$(date +%Y%m%d_%H%M).tar.gz -C /var/lib/bid-app projects/`
- `app/scripts/restore-backup.sh`：
  - 新增 `--with-files` 参数（默认 false）
  - 启用时 `tar -xzf projects_*.tar.gz -C /var/lib/bid-app/`
  - 二次确认提示「即将覆盖 /var/lib/bid-app/projects/，是否继续？」
- `app/IMPLEMENTATION_SPEC.md §24.2`：备份恢复章节重写

**新增依赖**：`bleach >= 6.1` → `pyproject.toml`

**测试**：
- `tests/test_blackboard.py`：原子写入失败回退、磁盘满 / 权限错；读路径缺失抛 `BlackboardMissing`
- `tests/test_html_sanitize.py`：脚本注入、内联样式、外链图片
- 手动跑一次 `pg-backup.sh` → `restore-backup.sh --with-files` 烟囱测试

**验收**：
- 抽取完成后 `/var/lib/bid-app/projects/{id}/blackboard.html` 存在 + DB `Project.blackboard_path` 一致
- 强杀容器后重启 → 没有 `.tmp` 残留（atomic rename 有效）
- 删除 Project → 磁盘目录消失
- LLM-1/2/3 prompt 调试日志确认 `blackboard_excerpt` 已注入

**依赖**：PR-M7-1 + PR-M7-2

---

## M8 — 人机交互扩展（M7 合并后可并行开发）

### PR-M8-1：材料理解先行（项 1）

**范围**：新增 interrupt 节点 + 前端页面 + decision API stage 扩展。

**新增 workflow 节点**：
- `app/backend/src/bid_app/workflow/nodes/material_understanding.py`：调 LLM-0（复用 LLM-1 模型，prompt 不同）→ 输出结构化 JSON `{核心需求, 评分要点, 模板风格, 关键约束, 风险标注}` → 写到 `WorkflowState.material_understanding`
- `app/backend/src/bid_app/workflow/nodes/material_understanding_review.py`：interrupt 节点
- `app/backend/src/bid_app/workflow/prompts/material_understanding.py`：ReAct 风格 prompt（Thought/Action/Observation 文本段落，**不上 tool-calling**）

**改动文件**：
- `app/backend/src/bid_app/workflow/graph.py`：插入新节点 `extract_documents → material_understanding → material_understanding_review (interrupt) → generate_outline`
- `app/backend/src/bid_app/api/projects.py`：`POST /projects/{id}/decision` 加 `stage: material_understanding | outline | chapter` 枚举
- `app/backend/src/bid_app/workflow/resolve.py`：根据 stage 决定 resume 走 LLM 还是直接 pass

**前端**：
- `app/frontend/src/pages/MaterialUnderstandingPage.tsx` 新建（按 UI-Track 风格）
  - serif 大标题「项目材料理解」
  - 5 个折叠分区显示 LLM-0 输出
  - pass / revise / skip 三按钮 + revise 文本框
- `app/frontend/src/router.tsx` 加路由 `/projects/:id/understanding`
- `app/frontend/src/api/projects.ts`：扩展 decision payload

**测试**：
- `tests/test_material_understanding_node.py`：mock LLM 返回 → 校验写入 state
- `tests/test_workflow_resume_understanding.py`：interrupt → POST decision → resume

**验收**：
- 上传 3 份文档后页面停在 `/projects/:id/understanding`
- pass → 跳转到 outline；revise + 文本 → LLM-0 重生成 → 再 interrupt；skip → 直接到 outline
- 前端显示新 UI（瑞典编辑风）

**依赖**：PR-M7-3（需要黑板才能让 LLM-0 引用）+ PR-UI-1

**决策注释 — 为什么 MVP 不上 tool-calling**（2026-05-13 与用户对齐，对应 IMPLEMENTATION_SPEC 待补 `D-EH`）：

ReAct 在本节点只用 prompt 内文本段落（Thought / Action / Observation / Final）模拟，不走 LiteLLM function calling 接口。理由按优先级：

1. **黑板已在 prompt 里，没东西可调**。这个节点唯一会想用的 tool 是"检索黑板"，但 PR-M7-3 已把黑板作为 `blackboard_excerpt` 变量直接注入 prompt（MVP 全文，超长才截断）。LLM 已经能看到，再加一个"读黑板"工具是空转。tool-calling 真正有价值是 LLM 需要做**它当下看不到的外部决策**时（查数据库、调 API、检索向量库），本节点没有这个需求。
2. **DashScope + LiteLLM 的 function calling 矩阵不稳**。通义千问系列只有部分模型版本支持 function calling，且 LiteLLM 对不同 provider 的 `tool_calls` 流式增量行为做了不同 wrapper。`services/llm.py` 当前三个 LLM 调用都是普通 completion，加 tool-calling 会扩出一套新的失败模式（partial tool_calls / parallel_tool_calls / tool_choice="required" 的兼容差异），测试矩阵翻倍。
3. **与 `max_tries=1`（D-AY）语义打架**。tool-calling 是多轮交互（LLM 出 tool_call → 系统执行 → 喂回 LLM → 再判断），任何一轮失败（网络 / tool 超时 / JSON 解析错）的 surface 路径与"失败一次到顶用户手动 retry"约定不兼容；中段失败硬塞到这个语义里很别扭。
4. **流式 + interrupt 节点的复用零摩擦**。普通 completion + 流式 + JSON 输出已能让前端逐字渲染；tool-calling 流式需要前端额外处理 `tool_calls` 增量片段，与现有 SSE 架构有摩擦。
5. **ReAct as text 已经够用**。prompt 内输出 `Thought / Action / Observation / Final` 段落保留链式推理的可读性（debug 时能看到思考路径），又不用维护 tool runtime。

**何时升级到 tool-calling**：未来引入"动态查招标公告 / 调评分规则数据库 / 读外部模板库"等真正的外部依赖时再做。届时核心价值是"让 LLM 决定**何时**调用**什么**外部能力"——目前所有数据都是静态且已经在 context 里，达不到这个触发条件。

---

### PR-M8-2：目录交互编辑（项 3）

**范围**：outline 从 markdown → 结构化 JSON，前端可拖拽编辑。

**改动 workflow**：
- `app/backend/src/bid_app/workflow/state.py`：`outline_json: list[OutlineNode]` 已在 PR-M7-1 加入；定义 `OutlineNode` Pydantic：`{id: str, title: str, level: int, description: str, children: list[OutlineNode]}`
- `app/backend/src/bid_app/workflow/nodes/generate_outline.py`：LLM-1 prompt 改为输出 JSON（用 LiteLLM JSON mode 或 schema-guided）
- `app/backend/src/bid_app/workflow/nodes/parse_outline.py`：校验 JSON + 给每个节点补 uuid
- `app/backend/src/bid_app/workflow/nodes/outline_review.py`：interrupt 行为支持两条恢复路径：
  - 用户编辑（PATCH outline_json）→ 直接 resume
  - 用户提反馈 → revise 走 LLM-1 重生成

**改动 API**：
- `app/backend/src/bid_app/api/projects.py`：
  - `PATCH /projects/{id}/outline`（鉴权 + state 必须在 `awaiting_outline_review`）
  - 扩展 `POST /projects/{id}/decision` 接受 `outline_patch` payload

**前端**：
- `app/frontend/src/pages/OutlineConfirmPage.tsx` 大改：
  - 引入 `@dnd-kit/sortable` + `@dnd-kit/core`（pnpm add）
  - 树形 UI：拖拽排序 + 增删改 + 层级调整
  - 顶部「锁定目录」按钮 → 写 `Project.locked_outline`
  - 反馈框 → revise 分支
- `app/frontend/src/components/OutlineTree.tsx` 新建
- `app/frontend/package.json`：新增 `@dnd-kit/*`

**测试**：
- `tests/test_outline_parse.py`：JSON 校验 / 缺字段 / 嵌套层级
- `tests/test_outline_patch_endpoint.py`：PATCH 鉴权 + state 校验
- Mock 模式同步实现 PATCH endpoint

**验收**：
- LLM-1 生成出可直接解析的 JSON outline
- 用户拖拽改章节顺序 → 后续 pick_chapter 按新序生成
- 用户提反馈 → LLM-1 重生成
- UI 按瑞典编辑风（PR-UI-1 tokens）

**依赖**：PR-M7-1（state schema）+ PR-UI-1

---

## M9 — 按目录选择性生成

### PR-M9-1：选择性生成 + 增量补齐（项 5 + D4）

**范围**：用户勾选章节、未选跳过、后续增量生成。

**改动 workflow**：
- `app/backend/src/bid_app/workflow/state.py`：`selected_chapter_ids: list[str]` 已在 PR-M7-1 加入
- `app/backend/src/bid_app/workflow/nodes/pick_chapter.py`：迭代源从 `outline.chapters` → `selected_chapter_ids`；未选章节根本不进流水线
- `app/backend/src/bid_app/workflow/nodes/assemble.py`：按 D4 直接跳过未选；章节编号保留 outline 原序号
- `app/backend/src/bid_app/workflow/prompts/write_chapter.py`：强约束「使用提供的 chapter_id 对应编号，不要自行重编」

**改动 API**：
- `app/backend/src/bid_app/api/projects.py`：
  - `POST /projects/{id}/generate-chapters` body `{chapter_ids: [...]}`：复用 `retry_failed_chapter_task` 路径，把未生成章节加入队列
- 鉴权 + state 校验（必须在 outline 已锁定之后）

**前端**：
- `app/frontend/src/pages/OutlineConfirmPage.tsx`：每个章节加 checkbox（默认全选）+「确认生成所选」按钮
- `app/frontend/src/pages/ProposalPage.tsx`（项目主页）：
  - 未生成章节灰显 + 单个「生成」按钮（触发 `generate-chapters`）
  - 已生成章节显示「导出本章」按钮（复用 PR-M6-2）
  - 顶部进度条：`已生成 / 已选 / 总章节`

**测试**：
- `tests/test_pick_chapter_filtered.py`：mock state `selected=[ch1, ch3]` → 只看到 ch1/ch3 进 pick_chapter
- `tests/test_assemble_skip_unselected.py`：docx 不含未选章节，编号是 1/3 而不是重编为 1/2
- `tests/test_incremental_generate.py`：选了 1/3 跑完 → 再请求生成 5 → assemble 后含 1/3/5

**验收**：
- 选 1/3/5 生成 → docx 内只含 1/3/5，编号一致
- 后续单独生成 2 → docx 重新组装为 1/2/3/5
- 每个 pass 状态章节都能独立导出（PR-M6-2 已就绪）

**依赖**：PR-M8-2（结构化目录）+ PR-M6-2（单章导出）

---

## PR 合并顺序总览

```
PR-UI-1  ──┐
           ├──▶ PR-M6-1 ──▶ PR-M6-2 ──▶ PR-M7-1 ──▶ PR-M7-2 ──▶ PR-M7-3 ──┐
                                                                            │
PR-UI-2  ──▶ (随 M6 完成后并行做)                                          │
                                                                            ▼
                                                              PR-M8-1 ┐
                                                              PR-M8-2 ┤── 并行
                                                                       │
                                                                       ▼
                                                                  PR-M9-1
```

- UI-Track 在 M6 启动前先合 UI-1；UI-2 与 M6/M7 并行
- M7 内部强依赖 M7-1 先合（schema 一次性迁）
- M8 两个 PR 互不依赖，可两人并行
- M9 必须等 M8-2 和 M6-2

---

## 上线 v2 总 checklist（最后一刀）

- [ ] 全部 PR 合并到 main
- [ ] `pg-backup.sh` + `projects/` tar 备份手动跑一次校验
- [ ] `restore-backup.sh --with-files` 在 staging 演练成功
- [ ] 横幅公告 24h 维护窗
- [ ] 部署：`./scripts/restart-after-update.sh`
- [ ] 执行：`docker compose exec app python -m bid_app.cli.flush_running_workflows --confirm`
- [ ] 启动横幅打印的 `BID_APP_MASTER_KEY` sha256 与备份比对
- [ ] 创建一个测试项目跑完整流水线（上传 → 理解 → 目录编辑 → 选择性生成 → 章节导出 + 整本导出）
- [ ] `IMPLEMENTATION_SPEC.md` v2 决策号（D-EF 起）已补齐
- [ ] `REQUIREMENTS.md` 新增 FR / NFR 已补齐
- [ ] `RUNTIME_TEST_REPORT.md` v2 烟囱测试章节已补齐
- [ ] 用户通知发送
