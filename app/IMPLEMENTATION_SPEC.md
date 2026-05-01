# 投标技术方案生成器 · 实施 Spec v2

> **配套文档**:`REQUIREMENTS.md` v0.5(讲"做什么"),本文档讲"怎么做"。
> **版本**:v2 (2026-05-01)。基于 v1 修正了 17 处技术问题,改写后偏向"复制即跑"的可执行度。
> **目标读者**:实施工程师 / 后续 Claude Code 会话 / 审稿同事。
> **使用方式**:从 §3 开始按顺序施工;每个里程碑章节(§22)对应明确的可验收输出。

---

## 0. 文档导航

| § | 内容 | 谁先看 |
|---|---|---|
| §1 | 目标与作用域(1 页) | 所有人 |
| §2 | 单页架构总图(数据流 / 控制流 / 持久化) | 所有人 |
| §3 | 关键设计决定与 v1 修正项 | 所有人 |
| §4 | 项目目录结构 | 开发者 |
| §5 | 技术栈与版本(含完整 `pyproject.toml` / `package.json`) | 开发者 |
| §6 | 环境变量清单 + 密钥生成 | 部署者 |
| §7 | 本地开发环境搭建 | 开发者首日 |
| §8 | 数据模型 — 10 张表 SQLAlchemy 完整代码 | M1 |
| §9 | Alembic Migration 0001(完整可执行) | M1 |
| §10 | LangGraph 工作流(state / 节点 / interrupt / retry / 状态同步) | M1 |
| §11 | LLM 服务(流式 + 重试 + 超时 + token 记账) | M1 |
| §12 | SSE 事件总线与章节流式推送链路 | M1 |
| §13 | DOCX 流水线(mermaid + pandoc + 串行锁) | M3 |
| §14 | 认证与安全(JWT / bcrypt / AES-GCM / 限流 / 428) | M2 |
| §15 | API 路由实现要点 + curl 验收 | M1+M2 |
| §16 | 前端实施(Vite + React + react-markdown + SSE + 路由) | M4 |
| §17 | Docker 与 docker compose(supervisord 多进程) | M5 |
| §18 | 测试策略 + conftest 关键 fixture | 全程 |
| §19 | 日志与可观测(structlog + trace_id) | 全程 |
| §20 | 状态机转换图(Project / Run / Chapter) | M1 |
| §21 | 风险与缓解 | 评审 |
| §22 | 里程碑施工清单(M0–M5,逐日动作) | 项目经理 |
| §23 | 验收 Checklist | 验收 |
| §24 | 附录(CLI 工具 / 备份恢复 / 密钥轮换) | 运维 |

---

## 1. 目标与作用域

把 v10 设计的 Dify 工作流落地为独立 Python web app,内网服务器部署,~10 用户共享池协作生成投标技术方案。

**核心边界**:
- 后端:Python 3.12 / FastAPI / LangGraph 0.6 / arq / PostgreSQL 16 / Redis 7
- 前端:Vite + React 18 + TypeScript + react-markdown + TanStack Query + Tailwind + shadcn/ui
- 部署:docker compose(3 容器:app / postgres / redis,**不引入 nginx**)
- LLM:DashScope(deepseek-v4-flash + qwen3.6-max-preview + qwen3.6-flash)经 LiteLLM 统一调
- DOCX:Pandoc 直转 + mermaid-cli 预渲染 PNG(不套公司模板)

**绝不做**:多租户 / SSO / 在线协作编辑 / PDF 解析 / 公网部署。

---

## 2. 单页架构总图

```
                 浏览器(内网,10 人在线)
                       │
                       │ HTTP :12123 cookie
                       ▼
   ┌────────────────────────────────────────────────┐
   │ Docker host(Linux 2c4g, TZ=Asia/Shanghai)     │
   │                                                │
   │ ┌─ container app ────────────────────────────┐ │
   │ │ supervisord                                 │ │
   │ │ ├─ uvicorn (FastAPI app)                    │ │
   │ │ │   ├─ /api/auth/* /api/me/* /api/admin/*  │ │
   │ │ │   ├─ /api/projects/* /api/projects/.../  │ │
   │ │ │   │     stream (SSE)                      │ │
   │ │ │   └─ /(*)  ← React dist + SPA fallback   │ │
   │ │ │                                           │ │
   │ │ │   写 → DB / 入队 → Redis(arq)            │ │
   │ │ │   读 ← DB / EventBus(asyncio.Queue)      │ │
   │ │ │                                           │ │
   │ │ └─ arq worker (独立 Python 进程,共用 .env)│ │
   │ │     ├─ run_workflow_task                    │ │
   │ │     │   └─ LangGraph + AsyncPostgresSaver  │ │
   │ │     │       ├─ extract → outline → ...     │ │
   │ │     │       ├─ write_chapter (LLM-2 流式)  │ │
   │ │     │       │   └─→ EventBus → SSE         │ │
   │ │     │       └─ interrupt @ human_review     │ │
   │ │     └─ generate_docx_task(模块级 Lock 串行)│ │
   │ └─────────────────────────────────────────────┘ │
   │                                                │
   │ ┌─ container postgres ─┐  ┌─ container redis ─┐│
   │ │ - 业务 10 表          │  │ - arq 任务队列    ││
   │ │ - LangGraph checkpoint│  │ - 限流计数        ││
   │ │ - TokenUsage 计费    │  │ - EventBus(可选) ││
   │ └──────────────────────┘  └───────────────────┘│
   │                                                │
   │ 挂载卷 (host: /var/lib/bid-app/)                │
   │  ├─ postgres-data/                             │
   │  ├─ projects/{id}/{tech_spec.docx, *.md, *.docx, docx-build/} │
   │  └─ backups/  ← 每日 03:00 pg_dump 7d 滚动     │
   └────────────────────────────────────────────────┘
                       │ HTTPS
                       ▼
              阿里云 DashScope(LLM 调用)
```

**关键并行关系**:
- **uvicorn 与 arq 是两个独立 Python 进程**(由 supervisord 管理),通过 Redis 队列 + Postgres 共享状态。这比 v1 的"同进程 asyncio.create_task"更稳:进程崩了独立重启不互拖。
- **EventBus 用 asyncio.Queue 实现,但前提是同进程**。因为生产者(arq worker 节点)和消费者(uvicorn SSE 端点)分进程,需要走 **Redis pub/sub** 中转。详见 §12。
- LangGraph checkpoint 在 PostgreSQL 持久化,任意进程重启工作流可从 checkpoint 续跑(NFR-2)。

---

## 3. 关键设计决定与 v1 修正项

### 3.1 设计决定(均带 rationale)

| # | 决定 | Rationale |
|---|---|---|
| **D-A** | uvicorn 与 arq 走 **两个进程**,supervisord 编排 | 单进程 asyncio.create_task 跑 arq 一旦 worker 崩了拖垮 HTTP;分进程隔离更稳 |
| **D-B** | EventBus 用 **Redis pub/sub** 跨进程,FastAPI 端订阅频道转 SSE | 因为 D-A 拆了进程,asyncio.Queue 不能跨进程 |
| **D-C** | API Key **运行时从 DB 取**,不进任何 LangGraph state/config | 防止密钥被 PostgresSaver checkpoint 落库 |
| **D-D** | LangGraph 节点封装 **整个流式收集** 在 `asyncio.wait_for(timeout=600)` 内 | FR-3.10 的 10 分钟必须包住完整流式过程,不是单次 await |
| **D-E** | 章节渲染前端用 **react-markdown**(不用 Tiptap) | 审核场景只读 + 流式 append 字符串更简单;Tiptap 是给可编辑场景的过度选型 |
| **D-F** | `must_change_password` 用 **FastAPI dependency** 拦截而非 starlette middleware | 中间件无法拿 user 上下文;dependency 自然取到当前用户 |
| **D-G** | Health check **只查 db / redis**,LLM 连通单独 `/api/me/api-key/test` | 健康检查应快、应只查内部依赖,不应被外网拖慢 |
| **D-H** | DOCX 串行用 **module-level `asyncio.Lock` + Redis lock 双保险** | arq 可能跨多个 worker 进程(未来扩容);Redis lock 保证 chromium 同时只一个 |
| **D-I** | Chapter retry 实现:**arq 重新入队同一 thread_id**,LangGraph 自动从 checkpoint 续跑 | 比 `Command(goto="...")` 简单且不需要修改 graph 结构 |
| **D-J** | 工作流节点状态变化时,**通过 hooks 同步到 DB Chapter 表**,SSE 事件由 EventBus 异步推 | DB 是真相源;EventBus 失败不影响数据正确性 |

### 3.2 v1 → v2 修正项一览

| 修正 | 原 v1 错误 | v2 正确做法 | 章节 |
|---|---|---|---|
| 1 | API Key 进 `WorkflowState` | DB 实时取,LLM 调用前传入 | §10.1 §11.2 |
| 2 | `wait_for(call_llm())` | `wait_for(collect_stream())` 包整个流 | §11.3 |
| 3 | Tiptap 做只读渲染 | react-markdown + remark-gfm + mermaid 自渲 | §16.4 |
| 4 | Dockerfile `CMD sh ... &` | supervisord 编排两进程 | §17.3 |
| 5 | must_change_password middleware | FastAPI dependency 拦截 | §14.5 |
| 6 | health 查 LLM | 只查 db/redis,LLM 单独端点 | §15.7 |
| 7 | DOCX 串行 "asyncio.Lock 跨 job" 一句话 | 模块级 Lock + Redis lock 双层 | §13.3 |
| 8 | mermaid config 不全 | 完整含中文字体 themeCSS | §13.2 |
| 9 | Chapter retry 模糊 | arq 重入 + thread_id + 重置 retry_count | §10.5 |
| 10 | 缺 update_state 代码 | 完整状态机大脑实现 | §10.4 |
| 11 | 缺流式 → SSE 链路 | trace 一遍 token 路径 | §12 |
| 12 | 数据模型 1 张表 | 10 张表全给 | §8 |
| 13 | Migration 不可执行 | 完整可跑 0001 | §9 |
| 14 | 缺 prompts | 给完整 LLM-1/2/3 prompt(对应 v10 §4.3/4.5.2/4.5.3) | §10.3 |
| 15 | 缺状态机图 | 给三层状态图 | §20 |
| 16 | 缺 SPA fallback | FastAPI catch-all 路由 | §15.6 |
| 17 | 缺 trace_id | structlog.contextvars 注入 trace_id | §19.2 |

---

## 4. 项目目录结构

```
bid/
├── 技术方案自动生成工作流 — Dify 搭建指南(含人工审核).md   ← v10 设计文档
├── CLAUDE.md
└── app/
    ├── REQUIREMENTS.md
    ├── IMPLEMENTATION_SPEC.md          ← 本文档
    ├── README.md                       ← M5 写
    ├── .env.example
    ├── docker-compose.yml
    ├── docker-compose.dev.yml          ← 仅 db + redis,开发用
    ├── Dockerfile
    ├── .gitignore
    ├── .pre-commit-config.yaml
    │
    ├── docker/                         ← Docker 辅助文件
    │   ├── supervisord.conf
    │   ├── mermaid-config.json
    │   ├── puppeteer-config.json
    │   └── pg-backup.sh                ← cron 调用的备份脚本
    │
    ├── scripts/                        ← 部署/维护脚本
    │   ├── install.sh                  ← M5 一键部署
    │   ├── gen-secrets.sh              ← 生成 master_key / jwt_secret
    │   ├── reset-admin.py              ← 应急重置 admin 密码
    │   └── restore-backup.sh           ← 备份恢复
    │
    ├── backend/
    │   ├── pyproject.toml
    │   ├── uv.lock
    │   ├── alembic.ini
    │   ├── src/bid_app/
    │   │   ├── __init__.py
    │   │   ├── main.py                 ← FastAPI app
    │   │   ├── config.py               ← Pydantic Settings
    │   │   ├── db.py                   ← async engine + session factory
    │   │   ├── deps.py                 ← FastAPI dependencies
    │   │   │
    │   │   ├── models/                 ← SQLAlchemy 2.x 模型(10 张表)
    │   │   │   ├── __init__.py
    │   │   │   ├── base.py             ← DeclarativeBase
    │   │   │   ├── user.py
    │   │   │   ├── api_key.py
    │   │   │   ├── project.py
    │   │   │   ├── document.py
    │   │   │   ├── run.py
    │   │   │   ├── chapter.py
    │   │   │   ├── chapter_version.py
    │   │   │   ├── review_event.py
    │   │   │   ├── token_usage.py
    │   │   │   └── docx_job.py
    │   │   │
    │   │   ├── schemas/                ← Pydantic v2 IO 模型
    │   │   ├── api/                    ← FastAPI 路由(8 个文件)
    │   │   ├── core/                   ← 通用基础设施
    │   │   │   ├── security.py         ← bcrypt + JWT
    │   │   │   ├── crypto.py           ← AES-GCM
    │   │   │   ├── rate_limit.py       ← slowapi
    │   │   │   ├── logging.py          ← structlog + trace_id
    │   │   │   └── errors.py           ← 自定义异常 + 转 HTTPException
    │   │   │
    │   │   ├── services/               ← 业务服务
    │   │   │   ├── document_extractor.py
    │   │   │   ├── llm.py              ← LiteLLM 包装 + 重试 + 超时
    │   │   │   ├── docx_export.py      ← mermaid + pandoc 流水线
    │   │   │   ├── api_key_validator.py
    │   │   │   └── token_usage.py
    │   │   │
    │   │   ├── workflow/
    │   │   │   ├── state.py            ← WorkflowState
    │   │   │   ├── graph.py            ← StateGraph 编译
    │   │   │   ├── checkpointer.py     ← AsyncPostgresSaver factory
    │   │   │   ├── runner.py           ← 工作流入口(arq 调它)
    │   │   │   ├── nodes/              ← 10 个节点
    │   │   │   ├── prompts/            ← 硬编码提示词(对应 v10 §4)
    │   │   │   └── sync.py             ← state ↔ DB 同步钩子
    │   │   │
    │   │   ├── worker/
    │   │   │   ├── settings.py         ← arq WorkerSettings
    │   │   │   └── tasks.py            ← run_workflow / generate_docx / retry
    │   │   │
    │   │   ├── events/
    │   │   │   ├── bus.py              ← Redis pub/sub 包装
    │   │   │   └── schemas.py          ← 事件类型枚举
    │   │   │
    │   │   └── cli/
    │   │       ├── run_local.py        ← M0 CLI
    │   │       ├── reset_admin.py      ← 应急重置
    │   │       └── test_llm.py         ← 测 DashScope 连通
    │   │
    │   ├── migrations/
    │   │   ├── env.py
    │   │   └── versions/
    │   │       └── 0001_initial.py
    │   │
    │   ├── templates/
    │   │   └── reference.docx          ← M3 用 LibreOffice 手作
    │   │
    │   └── tests/
    │       ├── conftest.py
    │       ├── unit/
    │       ├── integration/
    │       └── e2e/
    │
    └── frontend/
        ├── package.json
        ├── pnpm-lock.yaml
        ├── vite.config.ts
        ├── tsconfig.json
        ├── tailwind.config.ts
        ├── index.html
        ├── components.json             ← shadcn 配置
        └── src/
            ├── main.tsx
            ├── App.tsx
            ├── router.tsx
            ├── api/                    ← TanStack Query hooks
            ├── hooks/
            │   ├── useAuth.ts
            │   ├── useSSE.ts
            │   └── useToast.ts
            ├── pages/                  ← P0 ~ P8
            ├── components/
            │   ├── ui/                 ← shadcn 复制下来的组件
            │   ├── ChapterPreview.tsx  ← react-markdown + mermaid
            │   ├── ChapterSidebar.tsx
            │   ├── ReviewActions.tsx
            │   ├── DataExportPanel.tsx
            │   └── DashScopeBanner.tsx ← D3 一次性提示
            ├── lib/
            │   ├── utils.ts
            │   ├── markdown.tsx        ← 自定义 mermaid 渲染
            │   └── types.ts
            └── styles/globals.css
```

---

## 5. 技术栈与版本

### 5.1 后端 `app/backend/pyproject.toml`

```toml
[project]
name = "bid-app"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<0.120",
  "uvicorn[standard]>=0.34,<0.40",
  "pydantic>=2.9,<3",
  "pydantic-settings>=2.6,<3",
  "sqlalchemy[asyncio]>=2.0.36,<2.1",
  "asyncpg>=0.30,<0.31",
  "alembic>=1.14,<2",
  "langgraph>=0.6,<0.7",
  "langgraph-checkpoint-postgres>=2.0,<3",
  "litellm>=1.55,<2",
  "arq>=0.26,<0.27",
  "redis>=5.2,<6",
  "passlib[bcrypt]>=1.7.4,<2",
  "pyjwt[crypto]>=2.10,<3",
  "cryptography>=44.0,<45",
  "slowapi>=0.1.9,<0.2",
  "markitdown[all]>=0.0.2",
  "python-multipart>=0.0.20,<0.1",
  "structlog>=24.4,<25",
  "httpx>=0.28,<0.29",
  "click>=8.1,<9",  # CLI
  "rich>=13.9,<14",  # CLI 漂亮输出
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.25",
  "pytest-cov>=6.0",
  "ruff>=0.8",
  "mypy>=1.13",
  "types-passlib",
]

[project.scripts]
bid-app-cli = "bid_app.cli.run_local:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/bid_app"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "A", "C4", "PT", "SIM", "RUF"]
ignore = ["E501"]  # line-length 由 formatter 管

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
```

### 5.2 前端 `app/frontend/package.json`

```json
{
  "name": "bid-app-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
    "format": "prettier --write 'src/**/*.{ts,tsx,css,json}'"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.62.0",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0",
    "rehype-raw": "^7.0.0",
    "mermaid": "^11.4.0",
    "tailwindcss": "^3.4.16",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.5.5",
    "lucide-react": "^0.469.0",
    "class-variance-authority": "^0.7.1",
    "@radix-ui/react-dialog": "^1.1.2",
    "@radix-ui/react-dropdown-menu": "^2.1.2",
    "@radix-ui/react-tabs": "^1.1.1",
    "@radix-ui/react-toast": "^1.2.2",
    "zod": "^3.24.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.0",
    "typescript": "^5.6.0",
    "@typescript-eslint/eslint-plugin": "^8.18.0",
    "@typescript-eslint/parser": "^8.18.0",
    "eslint": "^9.16.0",
    "eslint-plugin-react-hooks": "^5.1.0",
    "eslint-plugin-react-refresh": "^0.4.16",
    "prettier": "^3.4.2",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49"
  },
  "packageManager": "pnpm@9.15.0"
}
```

### 5.3 系统依赖(在 Dockerfile 装)

| 包 | 锁定版本 | 用途 |
|---|---|---|
| `pandoc` | apt 提供的 (Debian 12: 2.17;若需 3.x 用 deb 包) | md → docx |
| `@mermaid-js/mermaid-cli` | `11.4.0` | mermaid → PNG |
| `chromium` | apt 提供的 (Debian 12) | mermaid-cli 依赖 |
| `fonts-noto-cjk` | apt | mermaid 渲染中文 |
| `supervisor` | apt | 进程编排 |
| `cron` | apt | pg_dump 定时 |

---

## 6. 环境变量清单

### 6.1 `.env.example`

```bash
# ─── 端口与时区 ────────────────────────────────
APP_PORT=12123
TZ=Asia/Shanghai

# ─── 数据库 ────────────────────────────────────
POSTGRES_USER=bid_app
POSTGRES_PASSWORD=__GENERATE_ME__
POSTGRES_DB=bid_app
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}
# 给 LangGraph PostgresSaver 用的 DSN(纯 psycopg/asyncpg 格式,不带 SQLAlchemy 前缀)
LANGGRAPH_DSN=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}

# ─── Redis ─────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ─── 加密密钥(必须,启动时校验)──────────────
# 生成命令:./scripts/gen-secrets.sh
BID_APP_MASTER_KEY=__64_HEX_CHARS__
JWT_SECRET=__64_HEX_CHARS__

# ─── 默认 admin(只在 0001 migration 写入) ──
ADMIN_DEFAULT_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=admin123

# ─── LLM 模型(D1)─────────────────────────────
LLM1_OUTLINE_MODEL=dashscope/deepseek-v4-flash
LLM2_CHAPTER_MODEL=dashscope/qwen3.6-max-preview
LLM3_VISUALS_MODEL=dashscope/qwen3.6-flash

# ─── 业务参数 ──────────────────────────────────
MAX_CONCURRENT_PROJECTS=10
MAX_FILE_SIZE_MB=50
DAILY_UPLOAD_QUOTA_MB=500
SINGLE_CHAPTER_TIMEOUT_SECONDS=600
LLM_RETRY_MAX=2
LLM_RETRY_BACKOFF_S=2,5

# ─── 路径(容器内)────────────────────────────
PROJECTS_DIR=/var/lib/bid-app/projects
BACKUPS_DIR=/var/lib/bid-app/backups
TEMPLATES_DIR=/app/backend/templates

# ─── 日志 ──────────────────────────────────────
LOG_LEVEL=INFO
```

### 6.2 `scripts/gen-secrets.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
echo "BID_APP_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
echo "JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
```

### 6.3 启动校验

`config.py` 用 `pydantic-settings` 强校验三个必须密钥的格式;不符合直接 `sys.exit(1)`,在 docker logs 打出明确错误。

---

## 7. 本地开发环境搭建

### 7.1 一次性安装

```bash
# Python + uv
brew install python@3.12 uv

# Node + pnpm
brew install node@20
corepack enable && corepack prepare pnpm@9.15.0 --activate

# 系统依赖(开发期跑 docx 流水线)
brew install pandoc
npm install -g @mermaid-js/mermaid-cli@11.4.0

# Docker
brew install --cask docker
```

### 7.2 起项目

```bash
cd app
./scripts/gen-secrets.sh > .env
cat .env.example >> .env  # 把模板剩余项追加(注意去重)

# 后端
cd backend
uv sync --all-extras
docker compose -f ../docker-compose.dev.yml up -d
uv run alembic upgrade head

# 终端 A:HTTP
uv run uvicorn bid_app.main:app --reload --port 12123 --host 127.0.0.1

# 终端 B:arq worker
uv run arq bid_app.worker.settings.WorkerSettings

# 终端 C:前端
cd ../frontend
pnpm install
pnpm dev      # 默认 5173,proxy /api → 12123
```

### 7.3 跑 M0 CLI

```bash
cd app/backend
uv run python -m bid_app.cli.run_local \
  --tech-spec ./samples/tech_spec.docx \
  --scoring  ./samples/scoring.docx \
  --template ./samples/template.docx \
  --api-key  sk-xxx \
  --pages-per-chapter 3 \
  --out      ./out
```

---

## 8. 数据模型 — 10 张表完整代码

`models/base.py`:

```python
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

`models/user.py`:

```python
from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user|admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`models/api_key.py`:

```python
from sqlalchemy import LargeBinary, ForeignKey, String, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin

class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="dashscope")
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)  # AES-GCM:nonce(12)+ciphertext
    last_validated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`models/project.py`:

```python
from sqlalchemy import String, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="init")
    # init | extracting | outlining | outline_ready | running | awaiting_review | done | failed | aborted
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    api_key_owner: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    dir_path: Mapped[str] = mapped_column(String(512))
    pages_per_chapter: Mapped[int] = mapped_column(default=3)
    max_retry_per_chapter: Mapped[int] = mapped_column(default=3)
```

`models/document.py`:

```python
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # tech_spec|scoring|template
    original_filename: Mapped[str] = mapped_column(String(255))
    markdown_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int] = mapped_column()
    extract_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
```

`models/run.py`:

```python
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    langgraph_thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    # running | done | failed | aborted
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
```

`models/chapter.py`:

```python
from sqlalchemy import String, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("run_id", "index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    key_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_pages: Mapped[int] = mapped_column(default=3)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|generating|awaiting_review|approved|skipped|failed
    retry_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
```

`models/chapter_version.py`:

```python
from sqlalchemy import String, ForeignKey, Text, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class ChapterVersion(Base, TimestampMixin):
    __tablename__ = "chapter_versions"
    __table_args__ = (UniqueConstraint("chapter_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    body_markdown: Mapped[str] = mapped_column(Text)
    feedback_in: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # approve|revise|skip|retry_failed|None(未审)
```

`models/review_event.py`:

```python
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class ReviewEvent(Base, TimestampMixin):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(16))  # approve|revise|skip|retry_failed
    feedback_text: Mapped[str | None] = mapped_column(String(4000), nullable=True)
```

`models/token_usage.py`:

```python
from sqlalchemy import String, ForeignKey, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class TokenUsage(Base, TimestampMixin):
    __tablename__ = "token_usage"
    __table_args__ = (
        Index("ix_token_usage_user_month", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
```

`models/docx_job.py`:

```python
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class DocxJob(Base, TimestampMixin):
    __tablename__ = "docx_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    arq_job_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|rendering_mermaid|pandoc|done|failed
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`models/__init__.py`:

```python
from .base import Base
from .user import User
from .api_key import ApiKey
from .project import Project
from .document import Document
from .run import Run
from .chapter import Chapter
from .chapter_version import ChapterVersion
from .review_event import ReviewEvent
from .token_usage import TokenUsage
from .docx_job import DocxJob

__all__ = [
    "Base", "User", "ApiKey", "Project", "Document", "Run",
    "Chapter", "ChapterVersion", "ReviewEvent", "TokenUsage", "DocxJob",
]
```

---

## 9. Alembic Migration 0001

`migrations/env.py` 关键片段:

```python
from bid_app.models import Base
target_metadata = Base.metadata

# 用环境变量里的 DATABASE_URL,但 alembic 自己用 sync 驱动
# 把 postgresql+asyncpg 改回 postgresql
def get_url() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")

config.set_main_option("sqlalchemy.url", get_url())
```

`migrations/versions/0001_initial.py`(完整):

```python
"""initial schema + default admin

Revision ID: 0001
Create Date: 2026-05-01
"""
import os
from alembic import op
import sqlalchemy as sa
from passlib.hash import bcrypt

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # api_keys
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="dashscope"),
        sa.Column("encrypted_key", sa.LargeBinary, nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "provider"),
    )

    # projects
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("status", sa.String(32), nullable=False, server_default="init"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("api_key_owner", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("dir_path", sa.String(512), nullable=False),
        sa.Column("pages_per_chapter", sa.Integer, nullable=False, server_default="3"),
        sa.Column("max_retry_per_chapter", sa.Integer, nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    # documents
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("markdown_path", sa.String(512)),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("extract_error", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # runs
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("langgraph_thread_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("error", sa.String(4000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # chapters
    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.String(1000)),
        sa.Column("key_points", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("target_pages", sa.Integer, nullable=False, server_default="3"),
        sa.Column("final_text", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(4000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "index"),
    )

    # chapter_versions
    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chapter_id", sa.Integer, sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body_markdown", sa.Text, nullable=False),
        sa.Column("feedback_in", sa.String(4000)),
        sa.Column("decision", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chapter_id", "version"),
    )

    # review_events
    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chapter_id", sa.Integer, sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_id", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("feedback_text", sa.String(4000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # token_usage
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_token_usage_user_month", "token_usage", ["user_id", "created_at"])

    # docx_jobs
    op.create_table(
        "docx_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("arq_job_id", sa.String(64), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(4000)),
        sa.Column("output_path", sa.String(512)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 默认 admin
    pwd = os.environ.get("ADMIN_DEFAULT_PASSWORD", "admin123")
    pwd_hash = bcrypt.using(rounds=12).hash(pwd)
    op.execute(
        sa.text(
            "INSERT INTO users (username, password_hash, role, is_active, must_change_password) "
            "VALUES (:u, :p, 'admin', true, true)"
        ).bindparams(u=os.environ.get("ADMIN_DEFAULT_USERNAME", "admin"), p=pwd_hash)
    )


def downgrade() -> None:
    for t in [
        "docx_jobs", "token_usage", "review_events", "chapter_versions",
        "chapters", "runs", "documents", "projects", "api_keys", "users",
    ]:
        op.drop_table(t)
```

> **LangGraph checkpoint 表**:`langgraph-checkpoint-postgres` 启动时自调 `await saver.setup()` 自动建,**不入** Alembic。

---

## 10. LangGraph 工作流 — 核心实现

### 10.1 State(`workflow/state.py`)

```python
from typing import TypedDict

class WorkflowState(TypedDict, total=False):
    # === 输入(只读)===
    project_id: int            # ⭐ DB 查询入口,API Key 通过 project 反查 owner 取
    run_id: int
    tech_spec_md: str
    scoring_md: str
    template_md: str
    pages_per_chapter: int
    max_retry_per_chapter: int

    # === v10 §3.3 五个 Loop 变量 ===
    chapters: list[dict]
    current_index: int
    retry_count: int
    finalized_chapters: list[str]
    revision_feedback: str

    # === Human Review 临时载体(由 interrupt 注入)===
    _review_decision: str   # approve | revise | skip
    _review_feedback: str

    # === 输出 ===
    final_proposal: str | None
```

> **⚠️ 不放 `api_key`**(D-C):防止被 PostgresSaver 落库。运行时通过 `project_id` → `Project.api_key_owner` → `ApiKey.encrypted_key` → 解密。

### 10.2 Graph(`workflow/graph.py`)

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from .state import WorkflowState
from .nodes import (
    extract_documents, generate_outline, parse_outline,
    pick_chapter, write_chapter, gen_visuals, merge_chapter,
    human_review, update_state, assemble,
)

def build_graph(checkpointer: AsyncPostgresSaver):
    g = StateGraph(WorkflowState)

    g.add_node("extract_documents", extract_documents.run)
    g.add_node("generate_outline", generate_outline.run)
    g.add_node("parse_outline", parse_outline.run)
    g.add_node("pick_chapter", pick_chapter.run)
    g.add_node("write_chapter", write_chapter.run)
    g.add_node("gen_visuals", gen_visuals.run)
    g.add_node("merge_chapter", merge_chapter.run)
    g.add_node("human_review", human_review.run)
    g.add_node("update_state", update_state.run)
    g.add_node("assemble", assemble.run)

    g.set_entry_point("extract_documents")
    g.add_edge("extract_documents", "generate_outline")
    g.add_edge("generate_outline", "parse_outline")
    g.add_edge("parse_outline", "pick_chapter")
    g.add_edge("pick_chapter", "write_chapter")
    g.add_edge("write_chapter", "gen_visuals")
    g.add_edge("gen_visuals", "merge_chapter")
    g.add_edge("merge_chapter", "human_review")
    g.add_edge("human_review", "update_state")
    g.add_conditional_edges(
        "update_state",
        lambda s: "pick_chapter" if s["current_index"] < len(s["chapters"]) else "assemble",
        {"pick_chapter": "pick_chapter", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)

    return g.compile(checkpointer=checkpointer)
```

### 10.3 提示词(`workflow/prompts/llm2_chapter.py` 节选)

完整提示词从 v10 设计文档 §4.5.2 移植。这里给关键片段:

```python
LLM2_SYSTEM = """你是一位资深技术方案撰写专家,深耕投标方案撰写 10 年以上。
你的任务是基于章节提纲、技术需求、打分规则,撰写一份对应章节的正文。

严格遵守:
1. 紧扣本章 key_points 展开,不偏题、不水
2. 紧扣 matched_scoring_items,让评委容易找到打分点
3. 输出纯 markdown(不要 markdown 代码块包裹整体内容)
4. 章节字数对齐 target_pages × ~600 字
5. 必要时用表格、列表、有序步骤增强可读性
"""

LLM2_USER_TEMPLATE = """请撰写以下章节的正文:

## 章节信息
- 标题: {title}
- 摘要: {summary}
- 关键点: {key_points}
- 对应打分项: {matched_scoring_items}
- 目标页数: {target_pages}

## 上下文(技术需求摘要)
{tech_spec_excerpt}

## 上下文(打分规则摘要)
{scoring_excerpt}

{revision_section}

请输出本章正文,markdown 格式,以 `# {title}` 作为章节起始。
"""

REVISION_TEMPLATE = """
## ⚠️ 上一轮审核反馈(请按反馈调整)
{revision_feedback}
"""

def build_messages(chapter: dict, tech_spec_md: str, scoring_md: str,
                   revision_feedback: str = "") -> list[dict]:
    revision_section = REVISION_TEMPLATE.format(revision_feedback=revision_feedback) \
        if revision_feedback else ""
    return [
        {"role": "system", "content": LLM2_SYSTEM},
        {"role": "user", "content": LLM2_USER_TEMPLATE.format(
            title=chapter["title"],
            summary=chapter["summary"],
            key_points="、".join(chapter["key_points"]),
            matched_scoring_items="、".join(chapter.get("matched_scoring_items", [])),
            target_pages=chapter["target_pages"],
            tech_spec_excerpt=_excerpt(tech_spec_md, 4000),
            scoring_excerpt=_excerpt(scoring_md, 2000),
            revision_section=revision_section,
        )},
    ]
```

LLM-1 / LLM-3 同模式,prompts 严格按 v10 §4.3 / §4.5.3 移植。

### 10.4 状态机大脑节点(`workflow/nodes/update_state.py`)

```python
"""状态机更新节点 — v10 §4.5.7。
读 _review_decision(由 human_review interrupt 注入),
决定 current_index / retry_count / finalized_chapters / revision_feedback 怎么变。"""

from ...services.token_usage import note_chapter_finalized
from ..state import WorkflowState
from ..sync import sync_chapter_to_db, publish_event


async def run(state: WorkflowState) -> dict:
    decision = state["_review_decision"]
    feedback = state.get("_review_feedback", "")
    current = state["current_index"]
    chapter = state["chapters"][current]

    pending_md: str = state.get("_pending_chapter_text", "")
    finalized = list(state["finalized_chapters"])

    if decision == "approve":
        finalized.append(pending_md)
        await sync_chapter_to_db(state["run_id"], current,
                                 status="approved", final_text=pending_md)
        await publish_event(state["project_id"], "chapter_approved",
                            chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    if decision == "skip":
        skip_marker = f"<!-- ⚠️ 章节《{chapter['title']}》被人工跳过 -->\n"
        finalized.append(skip_marker)
        await sync_chapter_to_db(state["run_id"], current,
                                 status="skipped", final_text=skip_marker)
        await publish_event(state["project_id"], "chapter_skipped",
                            chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    # decision == "revise"
    new_retry = state["retry_count"] + 1
    if new_retry >= state["max_retry_per_chapter"]:
        # 超限 → 强制 skip
        skip_marker = (f"<!-- ⚠️ 章节《{chapter['title']}》重写超限"
                       f"({new_retry}次)被强制累积 -->\n{pending_md}")
        finalized.append(skip_marker)
        await sync_chapter_to_db(state["run_id"], current,
                                 status="skipped", final_text=skip_marker)
        await publish_event(state["project_id"], "chapter_max_retry_skip",
                            chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    # 正常重写:current_index 不变,retry_count+1,带反馈
    await sync_chapter_to_db(state["run_id"], current,
                             status="generating", retry_count=new_retry)
    return {
        "current_index": current,
        "retry_count": new_retry,
        "revision_feedback": feedback,
    }
```

### 10.5 Chapter retry 实现(`workflow/runner.py` + `worker/tasks.py`)

**思路**(D-I):章节生成异常 → arq job 失败 → DB 章节标 `failed` → 用户点 retry 端点 → API 层重置 `retry_count=0` 并 enqueue 新 arq job(同 `thread_id`)→ LangGraph 从最近 checkpoint(进入 `write_chapter` 之前)续跑。

```python
# worker/tasks.py
async def run_workflow_task(ctx, *, project_id: int, run_id: int, thread_id: str):
    """启动或恢复工作流。"""
    settings_obj = ctx["settings"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)

    config = {"configurable": {"thread_id": thread_id}}
    state_loaded = await graph.aget_state(config)

    if state_loaded.values:
        # 已有 checkpoint → 续跑(初始 input 传 None)
        async for _ in graph.astream(None, config):
            pass
    else:
        # 全新启动
        state = await _build_initial_state(project_id, run_id)
        async for _ in graph.astream(state, config):
            pass


async def retry_failed_chapter_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                                    chapter_index: int):
    """手动重试 failed 章节:
       1. DB 把章节状态从 failed → pending,retry_count 不动(由 update_state 决定)
       2. 重新跑工作流(checkpoint 会从最后一个成功的 update_state 之后开始)"""
    async with get_db_session(ctx) as s:
        await s.execute(
            sa.text("UPDATE chapters SET status='pending', last_error=NULL "
                    "WHERE run_id=:r AND index=:i"),
            {"r": run_id, "i": chapter_index},
        )
        await s.commit()

    await run_workflow_task(ctx, project_id=project_id, run_id=run_id, thread_id=thread_id)
```

**为什么 D-I 比 `Command(goto=...)` 简单**:
- 不需要修改 graph 结构
- LangGraph checkpoint 已经记录了最后成功节点之后的 state,直接 invoke(None) 就续跑
- 失败时章节是停在 write_chapter / gen_visuals 之一抛了异常,没进 update_state,所以 checkpoint 是干净的"再来一遍这个章节"状态

### 10.6 state ↔ DB 同步(`workflow/sync.py`)

```python
"""把 LangGraph WorkflowState 的变化同步到 DB Chapter 表 + 发 SSE 事件。
为什么需要单独一层:LangGraph state 是 in-memory + checkpoint 持久化,
但前端展示用的是 DB Chapter 表(查询、列表都需要)。两者必须保持一致。"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import session_factory
from ..events.bus import event_bus

log = structlog.get_logger()


async def sync_chapter_to_db(run_id: int, index: int, **fields) -> None:
    async with session_factory() as s:
        await s.execute(
            sa.text(_build_update_sql(fields)),
            {"r": run_id, "i": index, **fields},
        )
        await s.commit()


async def publish_event(project_id: int, type_: str, **payload) -> None:
    try:
        await event_bus.publish(project_id, {"type": type_, **payload})
    except Exception:
        log.exception("event_publish_failed", project_id=project_id, type=type_)
```

---

## 11. LLM 服务 — 流式 + 重试 + 超时

### 11.1 入口(`services/llm.py`)

```python
"""LiteLLM 包装,实现 FR-3.9 重试 + FR-3.10 总时长包裹流式收集。

设计要点(D-D):超时必须包住"完整流式收集",不是单个 await。
所以 stream=True 的调用要这样写:

    async with asyncio.timeout(SINGLE_CHAPTER_TIMEOUT_SECONDS):
        async for chunk in stream:
            ...

不能写 `await asyncio.wait_for(litellm.acompletion(stream=True), 600)` —— 那只 timeout 第一个 token。"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import litellm
import structlog
from litellm.exceptions import RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout

from ..config import settings
from ..events.bus import event_bus
from .token_usage import record_token_usage

log = structlog.get_logger()


class LLMRetryFailed(Exception):
    pass


class LLMTimeoutExceeded(Exception):
    pass


_FAKE = os.environ.get("BID_APP_FAKE_LLM") == "1"


@dataclass
class StreamResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


async def call_llm_stream(
    *,
    model: str,
    messages: list[dict],
    api_key: str,
    user_id: int,
    project_id: int,
    run_id: int | None = None,
    chapter_index: int | None = None,
    **kw,
) -> StreamResult:
    """流式调用 + 重试 + 超时 + 推 SSE token + 记 token_usage。
    返回完整 markdown 与 token 统计。"""
    if _FAKE:
        return await _fake_stream(model, messages, project_id, chapter_index)

    backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
    last_err: Exception | None = None

    async with asyncio.timeout(settings.single_chapter_timeout_seconds):
        for attempt in range(settings.llm_retry_max + 1):
            try:
                return await _do_stream(model, messages, api_key, user_id, project_id,
                                        run_id, chapter_index, **kw)
            except (RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout) as e:
                last_err = e
                log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                raise LLMRetryFailed(str(e)) from e
            except Exception:
                # 4xx 等不重试
                raise

    raise LLMRetryFailed(str(last_err))


async def _do_stream(model, messages, api_key, user_id, project_id,
                     run_id, chapter_index, **kw) -> StreamResult:
    response = await litellm.acompletion(
        model=model, messages=messages, api_key=api_key,
        stream=True, stream_options={"include_usage": True}, **kw,
    )

    chunks: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    async for chunk in response:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            chunks.append(delta)
            if chapter_index is not None:
                await event_bus.publish(project_id, {
                    "type": "chapter_token",
                    "chapter_index": chapter_index,
                    "delta": delta,
                })
        if usage := getattr(chunk, "usage", None):
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

    text = "".join(chunks)
    await record_token_usage(
        user_id=user_id, project_id=project_id, run_id=run_id, model=model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
    )
    return StreamResult(text=text, prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens)


async def call_llm_json(
    *, model: str, messages: list[dict], api_key: str,
    user_id: int, project_id: int, run_id: int | None = None,
) -> tuple[dict, StreamResult]:
    """非流式(LLM-1 / LLM-3 用)+ 重试 + 超时 + JSON 解析。"""
    # 同上模式 stream=False;略
```

### 11.2 节点中如何取 API Key(`workflow/nodes/write_chapter.py`)

```python
"""LLM-2 节点 — v10 §4.5.2。
关键:api_key 不进 state,运行时从 DB 取(D-C)。"""

import asyncio
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...models import Project, ApiKey
from ...core.crypto import decrypt_api_key
from ..prompts.llm2_chapter import build_messages
from ..state import WorkflowState
from ..sync import sync_chapter_to_db, publish_event
from ...services.llm import call_llm_stream, LLMRetryFailed, LLMTimeoutExceeded


async def run(state: WorkflowState) -> dict:
    current = state["current_index"]
    chapter = state["chapters"][current]

    # API Key 实时取
    api_key = await _resolve_api_key(state["project_id"])

    # 通知前端章节开始
    await sync_chapter_to_db(state["run_id"], current, status="generating")
    await publish_event(state["project_id"], "chapter_started", chapter_index=current)

    messages = build_messages(
        chapter=chapter,
        tech_spec_md=state["tech_spec_md"],
        scoring_md=state["scoring_md"],
        revision_feedback=state.get("revision_feedback", ""),
    )

    try:
        result = await call_llm_stream(
            model=settings.llm2_chapter_model,
            messages=messages,
            api_key=api_key,
            user_id=await _resolve_user_id(state["project_id"]),
            project_id=state["project_id"],
            run_id=state["run_id"],
            chapter_index=current,
            temperature=0.6,
        )
    except (LLMRetryFailed, asyncio.TimeoutError) as e:
        await sync_chapter_to_db(state["run_id"], current,
                                 status="failed", last_error=str(e))
        await publish_event(state["project_id"], "chapter_failed",
                            chapter_index=current, reason=str(e))
        raise  # 抛出让 arq job 失败,等用户 retry

    # 把生成的章节正文保存为新版本
    await _save_chapter_version(state["run_id"], current, result.text,
                                feedback_in=state.get("revision_feedback", ""))

    return {"_pending_chapter_text": result.text}


async def _resolve_api_key(project_id: int) -> str:
    async with session_factory() as s:
        row = await s.execute(
            select(ApiKey.encrypted_key).join(Project, Project.api_key_owner == ApiKey.user_id)
            .where(Project.id == project_id, ApiKey.provider == "dashscope")
        )
        encrypted = row.scalar_one()
    return decrypt_api_key(encrypted)
```

### 11.3 超时与重试的关系图

```
asyncio.timeout(600s) ─────────────────────────────────────────────┐
  ▶ attempt 0: stream → first 5MB     1s        ✅ done            │
  或                                                                │
  ▶ attempt 0: stream...               2s 网络断 → RateLimitError   │
    sleep 2s                                                       │
  ▶ attempt 1: stream → first chunk → 服务挂 → ServiceUnavail      │
    sleep 5s                                                       │
  ▶ attempt 2: stream → 完整 90s      → done                       │
                                                                   │
  超过 600s 任意时刻 → asyncio.TimeoutError                          │
                                                                   │
  4xx (Key 错) → 直接抛,不重试                                     │
```

总时长 = ≤ 600s 包整个三次尝试 + 退避;符合 FR-3.10。

---

## 12. SSE 事件总线 — Redis Pub/Sub 跨进程

### 12.1 为什么不用 asyncio.Queue(D-B)

D-A 决定 uvicorn 与 arq 走两个进程:
- **生产者**:arq worker 进程内的 LangGraph 节点
- **消费者**:uvicorn 进程内的 SSE 端点

asyncio.Queue 只在同进程有效。跨进程要 Redis pub/sub。

### 12.2 实现(`events/bus.py`)

```python
"""跨进程事件总线。
- arq worker:调 publish() 把事件 RPUSH 到 Redis 频道
- uvicorn:订阅频道,SSE 端点把事件流给浏览器。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
import structlog

from ..config import settings

log = structlog.get_logger()


def _channel(project_id: int) -> str:
    return f"bid_app:events:project:{project_id}"


class EventBus:
    def __init__(self, url: str):
        self._url = url
        self._pub: redis_async.Redis | None = None

    async def start(self) -> None:
        self._pub = redis_async.from_url(self._url, decode_responses=True)
        await self._pub.ping()

    async def stop(self) -> None:
        if self._pub:
            await self._pub.aclose()

    async def publish(self, project_id: int, event: dict) -> None:
        if not self._pub:
            await self.start()
        await self._pub.publish(_channel(project_id), json.dumps(event, ensure_ascii=False))

    @asynccontextmanager
    async def subscribe(self, project_id: int) -> AsyncIterator[AsyncIterator[dict]]:
        client = redis_async.from_url(self._url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(_channel(project_id))

        async def gen():
            try:
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    yield json.loads(msg["data"])
            finally:
                pass

        try:
            yield gen()
        finally:
            await pubsub.unsubscribe(_channel(project_id))
            await pubsub.aclose()
            await client.aclose()


event_bus = EventBus(settings.redis_url)
```

### 12.3 SSE 端点(`api/stream.py`)

```python
import asyncio
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..deps import get_current_user
from ..events.bus import event_bus

router = APIRouter()

PING_INTERVAL = 20  # 每 20s 发一次心跳防止代理超时切断

@router.get("/{project_id}/stream")
async def stream(project_id: int, user=Depends(get_current_user)):
    async def gen():
        # 立即推一次 ready,告诉前端订阅成功
        yield "event: ready\ndata: {}\n\n"

        async with event_bus.subscribe(project_id) as events:
            async def heartbeat():
                while True:
                    await asyncio.sleep(PING_INTERVAL)
                    yield ": ping\n\n"

            ev_iter = events.__aiter__()
            while True:
                try:
                    ev = await asyncio.wait_for(ev_iter.__anext__(), timeout=PING_INTERVAL)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except StopAsyncIteration:
                    break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

### 12.4 一次完整 token 路径

```
arq worker(write_chapter 节点)
  call_llm_stream(model, messages, ...)
    └─ litellm.acompletion(stream=True)
        └─ async for chunk:  delta = "..."
              event_bus.publish(project_id, {type:"chapter_token", delta})
                  └─→ Redis PUBLISH bid_app:events:project:{id} '{"type":...}'

uvicorn(SSE 端点)
  event_bus.subscribe(project_id)
    └─ Redis SUBSCRIBE bid_app:events:project:{id}
        └─ async for msg:  yield "data: {...}\n\n" 给浏览器

浏览器(EventSource)
  onmessage: setChapterText(prev + delta)
```

---

## 13. DOCX 导出流水线

### 13.1 主流程(`services/docx_export.py`)

```python
"""DOCX 导出 — D5 简化方案:mermaid 预渲染 + pandoc 直转。
全局串行(D-H):asyncio.Lock + Redis 锁双层。"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import redis.asyncio as redis_async
import structlog

from ..config import settings

log = structlog.get_logger()
_module_lock = asyncio.Lock()  # 同进程内锁
_REDIS_LOCK_KEY = "bid_app:lock:docx_export"
_REDIS_LOCK_TTL = 300  # 秒,大于一次 docx 生成耗时

MERMAID_RE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


async def export_docx(
    *, markdown: str, project_dir: Path, project_name: str,
    reference_doc: Path, redis_url: str,
) -> Path:
    """串行化包装。返回最终 docx 路径。"""
    async with _module_lock:  # 进程内
        async with _redis_lock(redis_url):  # 跨进程(未来扩容)
            return await _export_docx_inner(markdown, project_dir, project_name, reference_doc)


async def _redis_lock(redis_url: str):
    """简易 Redis 互斥锁。"""
    r = redis_async.from_url(redis_url)
    token = str(asyncio.get_event_loop().time())
    while True:
        ok = await r.set(_REDIS_LOCK_KEY, token, nx=True, ex=_REDIS_LOCK_TTL)
        if ok:
            break
        await asyncio.sleep(0.5)
    try:
        yield
    finally:
        # 简化:直接 del(生产可改成 Lua 脚本判断 token)
        await r.delete(_REDIS_LOCK_KEY)
        await r.aclose()


async def _export_docx_inner(markdown: str, project_dir: Path,
                             project_name: str, reference_doc: Path) -> Path:
    work = project_dir / "docx-build"
    work.mkdir(parents=True, exist_ok=True)

    # 1. mermaid 预渲染
    inlined = await _render_mermaid(markdown, work)

    md_path = work / "proposal_inlined.md"
    md_path.write_text(inlined, encoding="utf-8")

    today = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d")
    out_name = f"{_sanitize(project_name)}_技术方案_{today}.docx"
    out_path = project_dir / out_name

    # 2. pandoc 直转
    args = ["pandoc", str(md_path), "-o", str(out_path)]
    if reference_doc.exists():
        args.extend([f"--reference-doc={reference_doc}"])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {err.decode(errors='replace')}")

    return out_path


async def _render_mermaid(markdown: str, work: Path) -> str:
    blocks = MERMAID_RE.findall(markdown)
    for i, code in enumerate(blocks):
        src = work / f"mmd_{i}.mmd"
        png = work / f"mmd_{i}.png"
        src.write_text(code, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            "mmdc",
            "-i", str(src),
            "-o", str(png),
            "-b", "transparent",
            "-c", "/etc/mermaid-config.json",
            "-p", "/etc/puppeteer-config.json",
            "--cssFile", "/etc/mermaid.css",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode == 0 and png.exists():
            markdown = markdown.replace(
                f"```mermaid\n{code}\n```",
                f"![]({png})",
                1,
            )
        else:
            log.warning("mermaid_render_failed", index=i, error=err.decode(errors="replace"))
            # 失败 → 保留原代码块
    return markdown


def _sanitize(name: str) -> str:
    """文件名安全化。"""
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name)[:80]
```

### 13.2 mermaid 中文字体配置

`docker/mermaid-config.json`:

```json
{
  "theme": "default",
  "themeVariables": {
    "fontFamily": "Noto Sans CJK SC, sans-serif"
  },
  "flowchart": {
    "useMaxWidth": false,
    "htmlLabels": true
  }
}
```

`docker/puppeteer-config.json`:

```json
{
  "args": [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage"
  ],
  "executablePath": "/usr/bin/chromium",
  "headless": "new"
}
```

`docker/mermaid.css`(把字体强制成 Noto CJK):

```css
* { font-family: "Noto Sans CJK SC", sans-serif !important; }
```

### 13.3 arq 任务(`worker/tasks.py`)

```python
async def generate_docx_task(ctx, *, project_id: int, docx_job_id: int) -> dict:
    """串行锁在 export_docx 内部实现(D-H)。"""
    async with get_db_session(ctx) as s:
        await s.execute(
            sa.text("UPDATE docx_jobs SET status='rendering_mermaid' WHERE id=:i"),
            {"i": docx_job_id},
        )
        await s.commit()
        # 取项目 markdown + dir + name
        ...

    try:
        out_path = await export_docx(
            markdown=markdown, project_dir=project_dir,
            project_name=project_name, reference_doc=Path(settings.templates_dir) / "reference.docx",
            redis_url=settings.redis_url,
        )
    except Exception as e:
        async with get_db_session(ctx) as s:
            await s.execute(
                sa.text("UPDATE docx_jobs SET status='failed', error=:e, "
                        "finished_at=NOW() WHERE id=:i"),
                {"i": docx_job_id, "e": str(e)[:4000]},
            )
            await s.commit()
        raise

    async with get_db_session(ctx) as s:
        await s.execute(
            sa.text("UPDATE docx_jobs SET status='done', output_path=:p, "
                    "finished_at=NOW() WHERE id=:i"),
            {"i": docx_job_id, "p": str(out_path)},
        )
        await s.commit()

    return {"output_path": str(out_path)}
```

---

## 14. 认证与安全

### 14.1 加密(`core/crypto.py`)

```python
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..config import settings

_KEY = bytes.fromhex(settings.bid_app_master_key)
_AES = AESGCM(_KEY)


def encrypt_api_key(plaintext: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + _AES.encrypt(nonce, plaintext.encode("utf-8"), None)


def decrypt_api_key(blob: bytes) -> str:
    nonce, ct = blob[:12], blob[12:]
    return _AES.decrypt(nonce, ct, None).decode("utf-8")
```

### 14.2 密码与 JWT(`core/security.py`)

```python
from datetime import datetime, timedelta, timezone

import jwt
from passlib.hash import bcrypt as _bcrypt
from ..config import settings

ACCESS_TTL = timedelta(hours=2)
REFRESH_TTL = timedelta(days=7)


def hash_password(plain: str) -> str:
    return _bcrypt.using(rounds=12).hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.verify(plain, hashed)


def _make_token(user_id: int, kind: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "kind": kind, "iat": int(now.timestamp()),
               "exp": int((now + ttl).timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_access_token(user_id: int) -> str:
    return _make_token(user_id, "access", ACCESS_TTL)


def create_refresh_token(user_id: int) -> str:
    return _make_token(user_id, "refresh", REFRESH_TTL)


def decode_token(token: str, kind: str) -> int:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("kind") != kind:
        raise jwt.InvalidTokenError("token kind mismatch")
    return int(payload["sub"])
```

### 14.3 限流(`core/rate_limit.py`)

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    default_limits=["100/minute"],
)
```

`main.py` 注册:

```python
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

### 14.4 依赖注入(`deps.py`)

```python
from typing import Annotated
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .core.security import decode_token
from .db import session_factory
from .models import User


async def get_db() -> AsyncSession:
    async with session_factory() as s:
        yield s


async def _resolve_user(request: Request, db: AsyncSession) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no access token")
    try:
        user_id = decode_token(token, kind="access")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user inactive")
    return user


async def get_current_user(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    """⚠️ 严格版:must_change_password=true 直接抛 428(D-F)。
    豁免端点用 get_current_user_lax。"""
    user = await _resolve_user(request, db)
    if user.must_change_password:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            detail={"error": "must_change_password"},
        )
    return user


async def get_current_user_lax(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    """宽松版:不检查 must_change_password。仅 /api/auth/me、/api/me/change-password、/api/auth/logout 用。"""
    return await _resolve_user(request, db)


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
```

### 14.5 完整登录流程示例(`api/auth.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rate_limit import limiter
from ..core.security import (
    create_access_token, create_refresh_token, decode_token, verify_password,
)
from ..deps import get_db, get_current_user_lax
from ..models import User
from ..schemas.auth import LoginRequest, MeResponse

router = APIRouter()


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已禁用")

    user.last_login_at = sa.func.now()
    await db.commit()

    response.set_cookie("access_token", create_access_token(user.id),
                        httponly=True, samesite="strict", max_age=2 * 3600, path="/")
    response.set_cookie("refresh_token", create_refresh_token(user.id),
                        httponly=True, samesite="strict", max_age=7 * 86400, path="/api/auth/refresh")
    return MeResponse.model_validate(user)


@router.post("/logout")
async def logout(response: Response, _=Depends(get_current_user_lax)):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/auth/refresh")
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user_lax)):
    return user
```

---

## 15. API 路由实现要点

### 15.1 项目相关(`api/projects.py`)

关键端点:

```python
@router.post("/{project_id}/start")
async def start_workflow(
    project_id: int,
    body: StartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_owned_or_412(db, project_id, user)

    # 检查 API Key
    api_key_row = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == "dashscope")
    )
    if api_key_row.scalar_one_or_none() is None:
        raise HTTPException(412, "请先配置 DashScope API Key")

    # 快照 api_key_owner
    project.api_key_owner = user.id
    project.status = "extracting"
    project.pages_per_chapter = body.pages_per_chapter
    project.max_retry_per_chapter = body.max_retry_per_chapter

    # 创建 Run + thread_id
    thread_id = f"run-{project_id}-{secrets.token_hex(8)}"
    run = Run(project_id=project_id, langgraph_thread_id=thread_id,
              started_at=datetime.now(timezone.utc), status="running")
    db.add(run)
    await db.commit()

    # 入队 arq job
    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "run_workflow_task",
        project_id=project_id, run_id=run.id, thread_id=thread_id,
    )
    return {"run_id": run.id}
```

### 15.2 章节审核与重试(`api/chapters.py`)

```python
@router.post("/{project_id}/chapters/{idx}/review")
async def review_chapter(
    project_id: int,
    idx: int,
    body: ReviewRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.decision not in ("approve", "revise", "skip"):
        raise HTTPException(400, "invalid decision")
    if body.decision == "revise" and not (body.feedback or "").strip():
        raise HTTPException(400, "revise must include feedback")

    run = await _get_active_run(db, project_id)
    db.add(ReviewEvent(
        chapter_id=(await _get_chapter(db, run.id, idx)).id,
        reviewer_id=user.id,
        decision=body.decision,
        feedback_text=body.feedback,
    ))
    await db.commit()

    # 用 LangGraph Command(resume=...) 恢复工作流
    saver = request.app.state.checkpointer
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": run.langgraph_thread_id}}

    # 在 arq 里 resume(因为 invoke 会触发后续节点的 LLM 调用)
    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "resume_workflow_task",
        project_id=project_id, run_id=run.id,
        thread_id=run.langgraph_thread_id,
        resume_payload={"decision": body.decision, "feedback": body.feedback or ""},
    )
    return {"ok": True}


@router.post("/{project_id}/chapters/{idx}/retry")
async def retry_chapter(
    project_id: int,
    idx: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await _get_active_run(db, project_id)
    chapter = await _get_chapter(db, run.id, idx)
    if chapter.status != "failed":
        raise HTTPException(409, f"chapter is {chapter.status}, not failed")

    db.add(ReviewEvent(chapter_id=chapter.id, reviewer_id=user.id, decision="retry_failed"))
    await db.commit()

    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "retry_failed_chapter_task",
        project_id=project_id, run_id=run.id,
        thread_id=run.langgraph_thread_id,
        chapter_index=idx,
    )
    return {"ok": True}
```

### 15.3 DOCX 生成与下载(`api/docx.py`)

```python
@router.post("/{project_id}/proposal.docx")
async def trigger_docx(
    project_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_done_project(db, project_id)

    # 已缓存 → 直接返回完成
    cached = Path(project.dir_path) / "proposal.docx"
    if cached.exists():
        return {"job_id": None, "cached": True}

    arq_pool = request.app.state.arq_pool
    job = await arq_pool.enqueue_job("generate_docx_task", project_id=project_id, docx_job_id=...)
    db.add(DocxJob(project_id=project_id, arq_job_id=job.job_id, status="pending"))
    await db.commit()
    return {"job_id": job.job_id, "cached": False}


@router.get("/{project_id}/proposal.docx")
async def download_docx(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_done_project(db, project_id)
    path = Path(project.dir_path) / "proposal.docx"
    if not path.exists():
        raise HTTPException(409, "请先 POST 触发生成")
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
```

### 15.4 健康检查(`api/health.py`)

```python
@router.get("/health")
async def health(request: Request):
    """只查 db + redis,不查 LLM(D-G)。"""
    checks = {"app": "ok"}
    try:
        async with session_factory() as s:
            await s.execute(sa.text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"fail: {e}"

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"fail: {e}"

    code = 200 if all(v == "ok" for v in checks.values()) else 503
    return JSONResponse(checks, status_code=code)
```

### 15.5 API Key 测试连通(`api/me.py`)

```python
@router.put("/api-key")
async def set_api_key(
    body: SetApiKeyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 先测试调用,通过才保存
    try:
        await api_key_validator.validate_dashscope(body.key)
    except Exception as e:
        raise HTTPException(400, f"API Key 验证失败:{e}")

    encrypted = encrypt_api_key(body.key)
    existing = (await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == "dashscope")
    )).scalar_one_or_none()
    if existing:
        existing.encrypted_key = encrypted
        existing.last_validated_at = sa.func.now()
        existing.updated_at = sa.func.now()
    else:
        db.add(ApiKey(user_id=user.id, provider="dashscope",
                      encrypted_key=encrypted, last_validated_at=sa.func.now()))
    await db.commit()
    return {"ok": True}
```

### 15.6 SPA fallback(`main.py` 末尾)

```python
from fastapi.responses import FileResponse

# 必须在所有 /api/* router 之后!
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """前端 React Router 的非 /api/* 路径,统一返回 index.html。"""
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(404)
    static_dir = Path("/app/frontend/dist")
    requested = static_dir / full_path
    if requested.is_file():
        return FileResponse(requested)
    return FileResponse(static_dir / "index.html")
```

---

## 16. 前端实施

### 16.1 路由(`router.tsx`)

```typescript
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { ProjectListPage } from './pages/ProjectListPage'
// ...
import { RequireAuth } from './hooks/useAuth'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/change-password', element: <RequireAuth allowMustChange><ChangePasswordPage /></RequireAuth> },
  { path: '/', element: <RequireAuth><ProjectListPage /></RequireAuth> },
  { path: '/projects/new', element: <RequireAuth><NewProjectPage /></RequireAuth> },
  { path: '/projects/:id/upload', element: <RequireAuth><DocumentUploadPage /></RequireAuth> },
  { path: '/projects/:id/outline', element: <RequireAuth><OutlineConfirmPage /></RequireAuth> },
  { path: '/projects/:id/review', element: <RequireAuth><ChapterReviewPage /></RequireAuth> },
  { path: '/projects/:id/proposal', element: <RequireAuth><ProposalPage /></RequireAuth> },
  { path: '/settings', element: <RequireAuth><SettingsPage /></RequireAuth> },
  { path: '/admin', element: <RequireAuth requireAdmin><AdminPage /></RequireAuth> },
  { path: '*', element: <Navigate to="/" /> },
])
```

### 16.2 API 客户端(`api/client.ts`)

```typescript
const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`)
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      ...(init.body && !(init.body instanceof FormData) && { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  })

  // 401 → /login;428 → /change-password。让上层 hook 处理。
  if (res.status === 204) return null as T
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, body)
  return body as T
}
```

### 16.3 SSE Hook(`hooks/useSSE.ts`)

```typescript
import { useEffect, useRef } from 'react'

export interface ProjectEvent {
  type: 'chapter_started' | 'chapter_token' | 'chapter_ready'
      | 'awaiting_review' | 'chapter_failed' | 'chapter_approved'
      | 'chapter_skipped' | 'proposal_ready' | 'error'
  chapter_index?: number
  delta?: string
  payload?: unknown
}

export function useProjectStream(
  projectId: number,
  onEvent: (e: ProjectEvent) => void,
) {
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    const es = new EventSource(`/api/projects/${projectId}/stream`, {
      withCredentials: true,
    })
    es.onmessage = (msg) => {
      try {
        handlerRef.current(JSON.parse(msg.data))
      } catch (e) {
        console.warn('SSE parse error', e, msg.data)
      }
    }
    return () => es.close()
  }, [projectId])
}
```

### 16.4 章节预览组件(`components/ChapterPreview.tsx`)

用 react-markdown,**不是 Tiptap**(D-E):

```typescript
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useRef } from 'react'
import mermaid from 'mermaid'

mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' })

export function ChapterPreview({ markdown }: { markdown: string }) {
  return (
    <div className="prose prose-slate max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '')
            if (match?.[1] === 'mermaid') {
              return <Mermaid code={String(children).trim()} />
            }
            return <code className={className} {...props}>{children}</code>
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

function Mermaid({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const id = `mermaid-${Math.random().toString(36).slice(2)}`
    mermaid.render(id, code).then(({ svg }) => {
      if (ref.current) ref.current.innerHTML = svg
    }).catch(() => {
      if (ref.current) ref.current.textContent = code
    })
  }, [code])
  return <div ref={ref} className="my-4" />
}
```

### 16.5 章节审核页核心逻辑(`pages/ChapterReviewPage.tsx`)

```typescript
export function ChapterReviewPage() {
  const { id } = useParams()
  const projectId = Number(id)
  const [chapters, setChapters] = useState<ChapterDTO[]>([])
  const [current, setCurrent] = useState(0)
  const [streamingText, setStreamingText] = useState('')

  const { data: detail } = useProjectDetail(projectId)
  useEffect(() => { if (detail) { setChapters(detail.chapters); setCurrent(detail.current_index) } }, [detail])

  useProjectStream(projectId, (e) => {
    if (e.type === 'chapter_token' && e.chapter_index === current) {
      setStreamingText((prev) => prev + (e.delta || ''))
    } else if (e.type === 'chapter_ready' && e.chapter_index === current) {
      setStreamingText('')  // 重置,下一章用
    } else if (e.type === 'awaiting_review') {
      // 提示用户审核
    } else if (e.type === 'chapter_failed') {
      // 显示重试按钮
    }
  })

  const submitReview = async (decision: 'approve' | 'revise' | 'skip', feedback?: string) => {
    await apiFetch(`/api/projects/${projectId}/chapters/${current}/review`, {
      method: 'POST',
      body: JSON.stringify({ decision, feedback }),
    })
  }

  return (
    <div className="grid grid-cols-[300px_1fr] h-screen">
      <ChapterSidebar chapters={chapters} current={current} />
      <main className="overflow-auto p-6">
        <ChapterPreview markdown={streamingText || chapters[current]?.final_text || ''} />
        <ReviewActions onReview={submitReview} status={chapters[current]?.status} />
      </main>
    </div>
  )
}
```

### 16.6 vite 配置 + Tailwind

`vite.config.ts`:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:12123',
      '/health': 'http://127.0.0.1:12123',
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
```

---

## 17. Docker 与 docker compose

### 17.1 Dockerfile(多阶段 + supervisord)

```dockerfile
# ──── Stage 1: 前端构建 ────
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ──── Stage 2: 后端运行时 ────
FROM python:3.12-slim-bookworm AS runtime

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# 系统依赖(一次 apt-get 装完)
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        chromium \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        nodejs \
        npm \
        curl \
        tzdata \
        supervisor \
        cron \
        ca-certificates \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# mermaid-cli
RUN npm install -g @mermaid-js/mermaid-cli@11.4.0

# Mermaid 配置
COPY docker/mermaid-config.json /etc/mermaid-config.json
COPY docker/puppeteer-config.json /etc/puppeteer-config.json
COPY docker/mermaid.css /etc/mermaid.css

# uv
RUN pip install --no-cache-dir uv

# Python 依赖
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
RUN cd /app/backend && uv sync --frozen --no-dev

# 后端代码
COPY backend/ /app/backend/

# 前端 dist
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# supervisord
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# cron 备份
COPY docker/pg-backup.sh /usr/local/bin/pg-backup.sh
RUN chmod +x /usr/local/bin/pg-backup.sh \
    && echo "0 3 * * * /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1" | crontab -

# 持久化目录(volume 会盖掉)
RUN mkdir -p /var/lib/bid-app/projects /var/lib/bid-app/backups

EXPOSE 12123

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf", "-n"]
```

### 17.2 supervisord.conf

```ini
[supervisord]
nodaemon=true
logfile=/var/log/supervisord.log
pidfile=/var/run/supervisord.pid

[program:migrate]
command=/app/backend/.venv/bin/python -m alembic -c /app/backend/alembic.ini upgrade head
directory=/app/backend
autostart=true
autorestart=false
startsecs=0
exitcodes=0
priority=10
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0

[program:uvicorn]
command=/app/backend/.venv/bin/uvicorn bid_app.main:app --host 0.0.0.0 --port 12123 --no-access-log
directory=/app/backend
autostart=true
autorestart=true
priority=20
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0

[program:arq-worker]
command=/app/backend/.venv/bin/arq bid_app.worker.settings.WorkerSettings
directory=/app/backend
autostart=true
autorestart=true
priority=20
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0

[program:cron]
command=/usr/sbin/cron -f
autostart=true
autorestart=true
priority=30
stdout_logfile=/dev/fd/1
stderr_logfile=/dev/fd/2
```

### 17.3 docker-compose.yml

```yaml
services:
  app:
    build:
      context: .
    image: bid-app:latest
    container_name: bid-app
    restart: unless-stopped
    ports:
      - "${APP_PORT:-12123}:12123"
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    volumes:
      - bid-projects:/var/lib/bid-app/projects
      - bid-backups:/var/lib/bid-app/backups
    networks: [bid-net]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:12123/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 60s

  postgres:
    image: postgres:16-alpine
    container_name: bid-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      TZ: Asia/Shanghai
    volumes:
      - bid-postgres-data:/var/lib/postgresql/data
    networks: [bid-net]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      retries: 6
    command: ["postgres", "-c", "shared_buffers=128MB", "-c", "max_connections=50"]

  redis:
    image: redis:7-alpine
    container_name: bid-redis
    restart: unless-stopped
    command: redis-server --maxmemory 100mb --maxmemory-policy allkeys-lru --appendonly yes
    volumes:
      - bid-redis-data:/data
    networks: [bid-net]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 6

volumes:
  bid-projects:
  bid-backups:
  bid-postgres-data:
  bid-redis-data:

networks:
  bid-net:
```

### 17.4 备份脚本 `docker/pg-backup.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

TS=$(date -d "today" +%Y%m%d_%H%M)
OUT="${BACKUPS_DIR}/bid_${TS}.dump"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -f "${OUT}"

# 滚动:保留 7 天
find "${BACKUPS_DIR}" -name "bid_*.dump" -mtime +7 -delete

echo "[$(date +%F\ %T)] backup ok → ${OUT}"
```

---

## 18. 测试策略

### 18.1 conftest.py 关键 fixture

```python
import os
os.environ.setdefault("BID_APP_FAKE_LLM", "1")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bid_app.main import app
from bid_app.models import Base
from bid_app.config import settings


@pytest_asyncio.fixture
async def db_engine():
    """每个测试一个全新的 schema(用 transaction-rollback 模式更快)。"""
    engine = create_async_engine(settings.database_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def fake_llm_outline_json():
    return {
        "chapters": [
            {"id": "ch_01", "title": "测试章节 1", "summary": "...",
             "key_points": ["a", "b"], "target_pages": 2,
             "matched_scoring_items": ["1.1"]},
            # 至少 3 章保证状态机走通
        ]
    }
```

### 18.2 LLM mock 实现(`services/llm.py` 的 `_FAKE` 分支)

```python
async def _fake_stream(model, messages, project_id, chapter_index) -> StreamResult:
    """BID_APP_FAKE_LLM=1 时用,不调外网。"""
    fake = "# 章节标题\n\n这是测试用的章节正文。" + "占位段落。" * 50
    # 模拟流式
    for ch in fake:
        await event_bus.publish(project_id, {
            "type": "chapter_token",
            "chapter_index": chapter_index,
            "delta": ch,
        })
    return StreamResult(text=fake, prompt_tokens=100, completion_tokens=200)
```

### 18.3 关键测试用例(M1 必须通过)

```
tests/integration/
├── test_auth.py
│   ├── test_login_success
│   ├── test_login_wrong_password_returns_401
│   ├── test_login_rate_limit_5_per_min
│   ├── test_must_change_password_returns_428
│   ├── test_change_password_clears_must_change_flag
│   └── test_logout_clears_cookie
├── test_api_key.py
│   ├── test_set_api_key_validates_via_dashscope_then_encrypts
│   ├── test_get_api_key_never_returns_plaintext
│   └── test_api_key_in_db_is_aes_gcm_encrypted
├── test_projects.py
│   ├── test_create_project_without_api_key_returns_412
│   ├── test_upload_pdf_returns_415
│   ├── test_team_shared_pool_visible_to_all
│   ├── test_only_creator_or_admin_can_delete
│   └── test_delete_project_cascades_files
├── test_workflow_e2e.py
│   ├── test_workflow_runs_end_to_end_with_fake_llm
│   ├── test_chapter_failed_after_3_retries
│   ├── test_chapter_retry_resumes_workflow
│   ├── test_revise_decision_increments_retry_count
│   ├── test_max_retry_per_chapter_forces_skip
│   └── test_chapter_timeout_marks_failed
└── test_docx.py
    ├── test_docx_export_serialization_lock
    ├── test_docx_export_with_mermaid
    └── test_docx_export_caches_after_first_run
```

### 18.4 状态机 e2e 测试模板

```python
@pytest.mark.asyncio
async def test_chapter_failed_after_3_retries(client, db_engine, monkeypatch):
    # 把 LLM mock 成"前 3 次抛 RateLimitError"
    call_count = [0]
    async def fail_3_times(*args, **kw):
        call_count[0] += 1
        if call_count[0] <= 3:
            raise RateLimitError("rate limited")
        ...
    monkeypatch.setattr("bid_app.services.llm._do_stream", fail_3_times)

    # 创建项目 + 上传 + 启动
    ...

    # 等 SSE chapter_failed
    async with client.stream("GET", f"/api/projects/{pid}/stream") as r:
        async for chunk in r.aiter_bytes():
            if b'"chapter_failed"' in chunk:
                break

    # 检查 DB 章节状态 = failed
    chapter = ...
    assert chapter.status == "failed"

    # 调 retry 端点 → 章节回到 pending
    monkeypatch.setattr("bid_app.services.llm._do_stream", normal_mock)
    r = await client.post(f"/api/projects/{pid}/chapters/0/retry")
    assert r.status_code == 200
    ...
    assert chapter.status == "approved"
```

---

## 19. 日志与可观测

### 19.1 structlog 配置(`core/logging.py`)

```python
import logging
import structlog
from structlog.contextvars import merge_contextvars
from ..config import settings


def setup_logging(level: str | None = None) -> None:
    level = (level or settings.log_level).upper()
    logging.basicConfig(level=level, format="%(message)s")

    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )
```

### 19.2 Trace ID 注入

```python
# core/middleware.py
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
import structlog


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
```

工作流节点也用 `bound_contextvars(run_id=..., chapter_index=..., trace_id=...)`,所有日志自动带这些字段,串起来一次请求的全链路。

### 19.3 健康检查输出示例

```json
{"app": "ok", "db": "ok", "redis": "ok"}
```

不通时 503:`{"app": "ok", "db": "fail: connection refused", "redis": "ok"}`。

---

## 20. 状态机转换图

### 20.1 Project 状态

```
init ──(上传完 3 文档)──→ extracting ──(抽取成功)──→ outlining ──(LLM-1 完成)──→ outline_ready
                                                                                       │
                                                                          (用户启动循环) ▼
                                                                                  running ◄──┐
                                                                                       │     │ revise
                                                                                       │     │
                                                                  (interrupt 暂停) ────┴──→ awaiting_review
                                                                                       │
                                                          (跑完所有章节 + assemble) ▼
                                                                                     done
                                                                                       │
                                                       (LLM 失败/超时,且无法 retry)──→ failed
                                                                                       │
                                                                         (用户 abort) ──→ aborted
```

### 20.2 Chapter 状态

```
                                  ┌── revise ←─────────┐
                                  │                    │
pending ──(generate 开始)──→ generating ──(generate 完成)──→ awaiting_review
            │                                              │
            │                          ┌─── approve ──→ approved (terminal)
            │                          ├─── skip ─────→ skipped (terminal)
            │                          └─── revise ───→ generating (上图回路)
            │
            ├── (LLM 3 次失败 / 超时) ──→ failed
            │                              │
            │                              └── (用户 retry) ──→ generating
            │
            └── (max_retry 触发强制累积) ──→ skipped
```

### 20.3 三层关系

- Project.status:用户视角的整体状态
- Run.status:本次工作流执行的状态
- Chapter.status:每章独立的状态
- 三者之间:**Project ← (聚合 Run) ← (聚合 Chapter)**。Project.status 变化由代码主动写,不要依赖任何字段触发器。

---

## 21. 风险与缓解(精炼)

| # | 风险 | 缓解 | 验证 |
|---|---|---|---|
| R1 | LiteLLM 对 DashScope `qwen3.6-max-preview` 不识别 | M0 第 0.5 天用 `python -m bid_app.cli.test_llm` 确认;不行改用 `dashscope` SDK 直连或换模型名 | M0 验收 |
| R2 | LangGraph + AsyncPostgresSaver 启动初次 setup() 偶发 hang | 在 lifespan 用 `asyncio.wait_for(setup(), 30)` 包,失败重启容器 | M1 |
| R3 | mermaid-cli 在 docker 里渲染中文乱码 | 装 fonts-noto-cjk + `mermaid.css` 强制字体 | M3 验收(导出含中文 mermaid 的 docx) |
| R4 | chromium 在低内存容器 OOM | 加 `--disable-dev-shm-usage` + 串行锁(D-H) | M3 + M5 6h 压测 |
| R5 | arq 进程崩了 in-flight job 丢 | arq 默认有 retry;加 `max_tries=3` + `keep_result=86400` | M1 |
| R6 | uvicorn 与 arq 同 .env 但分进程,settings 加载两次,密钥不一致 | gen-secrets 一次写到 .env,两进程读同一文件 | 启动后 logs 校验 |
| R7 | SSE 长连接被反向代理(未来加 nginx)切断 | 心跳每 20s `: ping`(已实现);nginx 加 `proxy_buffering off` 写进 README | 未来 |
| R8 | Postgres connection pool 被 LangGraph 与 SQLAlchemy 互相挤占 | LangGraph 用独立的 asyncpg pool;SQLAlchemy 池 size=10 | M1 测试 |
| R9 | 手作的 reference.docx 样式跑歪 | M3 第 1 天先空模板 + Pandoc 默认样式跑通,再手作 | M3 |
| R10 | 用户首次部署 BID_APP_MASTER_KEY 写错 → ApiKey 永久不可解密 | gen-secrets 强制生成 + 文档明确警告 + 启动校验长度 | 部署文档 |
| R11 | LangGraph state schema 改动后旧 checkpoint 不兼容 | 任何 state schema 变更 → 删 checkpoint 表(SQL 注释里写) | 升级文档 |
| R12 | DocxJob 状态卡 pending(arq 崩了) | DocxJob `created_at` > 30min 仍 pending → 标 failed,允许重试 | M3 |

---

## 22. 里程碑施工清单

### M0 · CLI 验证(1-2 天)

**目标**:在终端跑通 LangGraph + LiteLLM + DashScope + Pandoc + mermaid 全链路。

**Day 1**:
- [ ] `app/backend/pyproject.toml`(§5.1)
- [ ] `app/backend/src/bid_app/config.py`(§5)
- [ ] `app/backend/src/bid_app/cli/test_llm.py` — 用真实 Key 三模型各打一次
  - [ ] 验收:三模型都返回 ≥ 100 chars 的内容,无错
- [ ] `app/backend/src/bid_app/services/llm.py`(§11.1)
- [ ] `app/backend/src/bid_app/workflow/state.py`(§10.1)
- [ ] `app/backend/src/bid_app/workflow/prompts/` 完整移植 v10 §4.3 / 4.5.2 / 4.5.3 提示词

**Day 2**:
- [ ] 全部 10 个 workflow node(`workflow/nodes/`)
- [ ] `workflow/graph.py`(§10.2)+ SQLite checkpointer 简化版
- [ ] `services/document_extractor.py`(markitdown 包装)
- [ ] `services/docx_export.py`(§13.1,M0 阶段 mermaid 可选)
- [ ] `cli/run_local.py` — 命令行交互式审核 → 完整产出
- [ ] **验收**:跑一个真实投标样本(自己造或用户给),产出 markdown 字数 ≥ 5000、docx 在 Word 打开正常

### M1 · 后端核心 API(3-4 天)

**Day 1**:
- [ ] `models/` 全 10 张表(§8)
- [ ] `migrations/0001_initial.py`(§9)
- [ ] `db.py`(异步 engine + session_factory)
- [ ] `events/bus.py`(§12)
- [ ] `worker/settings.py` + `worker/tasks.py`(§10.5)

**Day 2**:
- [ ] `api/projects.py` 创建 / 列表 / 详情 / 删除
- [ ] `api/projects.py` `/start` 端点 + Run 创建 + arq enqueue
- [ ] `api/projects.py` `/documents` 上传 + markitdown 抽取
- [ ] `api/stream.py` SSE
- [ ] `workflow/sync.py` state ↔ DB

**Day 3**:
- [ ] `api/chapters.py` `/review` 端点 + `Command(resume=...)`
- [ ] `api/chapters.py` `/retry` 端点
- [ ] `api/projects.py` `/proposal` `/proposal.md`
- [ ] FR-3.10 超时测试(用 fake LLM mock 慢响应)
- [ ] FR-3.9 重试测试(mock 抛 RateLimitError 3 次)

**Day 4**:
- [ ] `services/api_key_validator.py`(测试调 DashScope 最小请求)
- [ ] M1 集成测试(§18.3 全部通过)
- [ ] `Dockerfile` + `docker-compose.dev.yml` + 可在 docker 里跑通完整流程
- [ ] **验收**:`curl` 能跑通完整流程含 SSE 章节流 + 章节 failed + retry 恢复

### M2 · 认证 + 用户管理 + API Key(2-3 天)

**Day 1**:
- [ ] `core/security.py` `core/crypto.py` `core/rate_limit.py`
- [ ] `deps.py`(§14.4)
- [ ] `api/auth.py`(§14.5)+ 限流测试
- [ ] `api/me.py` 改密 / API Key CRUD
- [ ] `api/admin.py`

**Day 2**:
- [ ] FR-6.6 must_change_password dependency 测试(7 个豁免端点 + 其他全 428)
- [ ] M2 集成测试全过

**Day 3**(buffer):部署在 dev docker 里 curl 跑一遍真人审核流。

### M3 · DOCX 导出(2 天)

**Day 1**:
- [ ] `templates/reference.docx` 用 LibreOffice 手作:中文字体 + Heading 1-4 + 表格基础边框
- [ ] `services/docx_export.py` 完整(§13.1)
- [ ] `worker/tasks.py` `generate_docx_task`(§13.3)
- [ ] `api/docx.py` POST/GET/job-status

**Day 2**:
- [ ] Dockerfile 完整(§17.1)+ supervisord(§17.2)
- [ ] mermaid 中文字体配置(§13.2)
- [ ] **验收**:跑含 3 个 mermaid 图 + 5 个表格的样本 → docx 在 Word 打开:章节标题层级、表格边框、PNG 中文不乱码、文件名 `项目名_技术方案_20260501.docx`、SLA < 15s

### M4 · 前端 v1(5-7 天)

**Day 1-2(M4.1)**:
- [ ] vite + tailwind + shadcn 初始化
- [ ] 路由 + `RequireAuth`
- [ ] LoginPage / ChangePasswordPage
- [ ] `apiFetch` + 401/428 跳转
- [ ] SettingsPage(API Key 配置 + 测试连通 + token 消费图表)

**Day 3(M4.2)**:
- [ ] ProjectListPage(团队共享池)
- [ ] NewProjectPage / DocumentUploadPage(.docx/.doc/.md/.txt 限制)
- [ ] OutlineConfirmPage(可选编辑)

**Day 4-5(M4.3 ⭐ 最难)**:
- [ ] ChapterReviewPage 主体 + 侧栏
- [ ] react-markdown + mermaid 自渲(§16.4)
- [ ] SSE 流式 token 涌入(§16.5)
- [ ] ReviewActions 三按钮 + 反馈框
- [ ] ChapterVersion 历史 tab
- [ ] failed 章节红标 + retry 按钮

**Day 6(M4.4)**:
- [ ] ProposalPage(全文预览 + 复制 + .md / .docx 下载 + 进度条)
- [ ] AdminPage(用户增删 + token 消费汇总)
- [ ] DashScopeBanner(D3 一次性提示)

**Day 7(buffer + 视觉打磨)**:
- [ ] 用 shadcn 把所有按钮、表单、表格统一风格
- [ ] **验收**:浏览器跑完整流程不出 console error;`failed` 重试可点;.docx 下载流程顺畅

### M5 · 部署打包(2-3 天)

**Day 1**:
- [ ] `docker-compose.yml` 完整(§17.3)
- [ ] `scripts/install.sh` `scripts/gen-secrets.sh` `scripts/restore-backup.sh`
- [ ] `docker/pg-backup.sh` + crontab
- [ ] `.env.example` 完整 + `README.md`
- [ ] 启动横幅(stdout 打印 `⚠️ 默认 admin/admin123 请立即改密` + master_key 哈希)

**Day 2**:
- [ ] 在 fresh Linux 服务器(2c4g Ubuntu 22.04)上跑 `./install.sh`,30 分钟内全部跑起来
- [ ] 跑一份真实投标方案 → 全流程验证
- [ ] 6 小时压力测试无 OOM
- [ ] 凌晨 03:00 检查 `/var/lib/bid-app/backups/bid_*.dump` 已生成

**Day 3(buffer)**:运维文档 + 备份恢复演练。

---

## 23. 验收 Checklist

总验收对应 REQUIREMENTS §14 已确认事项 15 条 + 仍待评审 7 条。

### 功能验收

- [ ] M0:三模型 + Pandoc + mermaid 各打 1 个 ✅
- [ ] 工作流端到端:5 章方案 → markdown ≥ 8000 字
- [ ] 章节 failed → retry → 恢复
- [ ] 章节超时 10 分钟(用 mock 慢 LLM 触发)
- [ ] revise → retry_count + 1;到上限自动 skip
- [ ] DOCX 含中文 mermaid + 表格,Word 打开无问题
- [ ] DOCX 串行(并发 2 个 docx job → 串行执行)
- [ ] 前端 8 个页面无 console error
- [ ] failed 章节红标 + retry 按钮可点

### 安全验收

- [ ] 改密前 428 拦截
- [ ] 5 次失败登录后该 IP 锁 5 分钟
- [ ] API Key 直接读 DB 看到的是 bytes,不是明文
- [ ] 默认 admin/admin123 + 必须改密 + 改密后 must_change_password=false
- [ ] DashScope banner 登录后显示

### 部署验收

- [ ] `docker compose up -d` 一键起 + healthcheck 全过
- [ ] 6 小时压力(模拟 3 个项目并发跑)无 OOM
- [ ] 凌晨 3 点 cron pg_dump 落到 backups 卷
- [ ] 容器重启后 in-flight 工作流从 checkpoint 续跑

### 主观验收

- [ ] 用户审核 5 章后口头反馈"可用"
- [ ] 导出 docx 给排版同事,30 分钟内能出片

---

## 24. 附录

### 24.1 应急 CLI

```bash
# 重置 admin 密码
docker compose exec app python -m bid_app.cli.reset_admin --password new_pass

# 测试 LLM 连通
docker compose exec app python -m bid_app.cli.test_llm --api-key sk-xxx

# 看实时日志
docker compose logs -f app | jq -c .
```

### 24.2 备份恢复

```bash
# 备份(平时 cron 自动)
docker compose exec postgres pg_dump -U bid_app -F c -f /tmp/manual.dump bid_app

# 恢复(灾难场景)
./scripts/restore-backup.sh /var/lib/bid-app/backups/bid_20260501.dump
# 内部:停 app → drop db → create db → pg_restore → 起 app
```

### 24.3 BID_APP_MASTER_KEY 轮换流程

⚠️ **危险操作**,只在 master key 泄漏时做:

```python
# scripts/rotate_master_key.py
# 1. 老 key 解密所有 ApiKey.encrypted_key → 明文
# 2. 新 key 重新加密 → 写回 DB
# 3. 改 .env 的 BID_APP_MASTER_KEY
# 4. 重启容器
```

写好后但不默认启用;每次运行需要 `--confirm` 显式确认。

### 24.4 关键 SQL 速查

```sql
-- 查某用户本月 token 消费
SELECT model, SUM(prompt_tokens) p, SUM(completion_tokens) c
FROM token_usage
WHERE user_id = ? AND created_at >= date_trunc('month', NOW())
GROUP BY model;

-- 查所有 failed 章节
SELECT p.name, c.index, c.title, c.last_error
FROM chapters c
JOIN runs r ON r.id = c.run_id
JOIN projects p ON p.id = r.project_id
WHERE c.status = 'failed';

-- 强制重置某章节为 pending(应急)
UPDATE chapters SET status='pending', last_error=NULL, retry_count=0
WHERE run_id=? AND index=?;
```

---

**文档结束。从 §22 M0 开始施工。**
