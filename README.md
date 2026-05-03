# 投标技术方案生成器（bid-app）

> 基于 LangGraph + 通义千问的中文投标技术方案自动生成与人工审核平台，把"读招标 → 拉提纲 → 写章节 → 审核修订 → 导出 docx"全链路装在一个 Web 应用里。

[![部署](https://img.shields.io/badge/部署-Docker_Compose-2496ED?logo=docker&logoColor=white)](app/README.md)
[![后端](https://img.shields.io/badge/后端-FastAPI%20%2B%20arq%20%2B%20LangGraph-009688?logo=fastapi&logoColor=white)]()
[![前端](https://img.shields.io/badge/前端-Vite%20%2B%20React%20%2B%20shadcn-646CFF?logo=vite&logoColor=white)]()
[![模型](https://img.shields.io/badge/模型-阿里百炼_DashScope-FF6A00)]()
[![License](https://img.shields.io/badge/License-内部使用-lightgrey)]()

---

## ✨ 它解决什么问题

写一份合格的投标技术方案要做的事：

1. 通读甲方技术规范书（几十到几百页）
2. 拆解评分细则、对照各项打分要求
3. 拟定章节大纲 + 与团队对齐
4. 逐章撰写专业内容（每章 3000-10000 字）
5. 反复修改、补图、调整重点
6. 排版导出 Word，给排版同事二次美化

**痛点**：人少、时间紧（5-7 天周期）、章节质量参差不齐、修改一轮重写一遍、Mermaid 图手画累。

**这个工具做了什么**：

- 📖 **三类文档自动抽取**：技术规范书 / 打分规则 / 模板范例，markitdown + LibreOffice 兼容 `.docx` `.doc` `.md` `.txt`
- 🧠 **三个 LLM 分工**：LLM-1 拟提纲、LLM-2 写正文（流式）、LLM-3 配可视化（Mermaid 流程图 / 架构图）
- ✅ **人工审核闭环**：每章生成后停下来，**三按钮决策**（通过 / 修订 / 跳过）；revise 时把上一轮正文 + 你的修改意见一起喂给 LLM 做有针对性修订
- 🔁 **状态机持久化**：用 LangGraph checkpoint 写 PostgreSQL，容器重启不丢进度，从最近成功节点续跑
- 📊 **流式打字 + 实时进度**：SSE 推 token，前端逐字渲染；全局进度横幅跨页可见
- 📄 **一键导出 .docx**：Pandoc + reference.docx + Mermaid PNG 中文字体，文件名 `项目名_技术方案_YYYYMMDD.docx`
- 👥 **团队共享池**：多用户可见同一项目，避免重复劳动

---

## 🏗️ 架构总览

```mermaid
flowchart LR
  U[用户浏览器] -->|HTTPS| FE[前端 SPA]
  FE -->|REST + SSE| API[FastAPI]
  API <-->|enqueue| ARQ[arq worker]
  ARQ -->|LangGraph| GRAPH[工作流图<br/>11 个节点]
  GRAPH -->|流式 token| LLM[百炼 DashScope<br/>qwen3.6-max / flash]
  GRAPH -->|checkpoint| PG[(PostgreSQL 16)]
  ARQ -->|缓存 / 队列 / 锁| REDIS[(Redis 7)]
  GRAPH -->|文件| FS[/var/lib/bid-app]
  ARQ -->|export| PANDOC[Pandoc + Mermaid CLI<br/>+ Chromium]
  PANDOC -->|.docx| FS
```

工作流的 11 个 LangGraph 节点（gen_visuals / merge_chapter / human_review 三节点拆分见 D-EE）：

```
extract_documents → generate_outline → outline_review (interrupt)
  → parse_outline → pick_chapter → write_chapter → gen_visuals
  → merge_chapter → human_review (interrupt) → update_state → assemble
```

---

## 🚀 快速开始

> 完整部署方案（生产）见 [`app/README.md`](app/README.md)。

### 本地开发（macOS + Colima 或 Docker Desktop）

```bash
git clone <repo-url> bid && cd bid/app

# 1. 一键生成 .env（master_key + JWT secret）
bash scripts/gen-secrets.sh

# 2. 把百炼 API Key 留空（用户登录后在 Settings 页配置即可）

# 3. 起容器
docker compose up -d

# 4. 等 healthcheck（约 30 秒）
docker compose ps
```

打开浏览器 `http://localhost:12123`：

- 默认账号：**admin / admin123**（首次登录强制改密）
- 改密后到 **设置 → API Key 配置**填入百炼 Key
- 新建项目，上传 1-3 份文档（技术规范书 / 打分规则 / 模板，至少 1 份）
- 点 **启动生成** → 看 LangGraph 跑 → 章节审核 → 导出 .docx

### 服务器部署（Ubuntu 22.04+）

```bash
# 一键安装：装 docker → clone → 生成 secrets → 起容器 → 等 healthy
curl -fsSL https://get.docker.com | sh
git clone <repo-url> bid && cd bid/app
sudo ./scripts/install.sh
```

30 分钟内能跑起来；2c4g 配置可支撑 10 人共享池 + 单项目并发 ≤ 10。

---

## 🧰 技术栈

| 层 | 选型 | 关键决策 |
|---|---|---|
| 前端 | Vite + React 18 + TypeScript + TanStack Query + shadcn/ui + Tailwind | SSE 流式 / Mermaid 客户端自渲 / mock 双模式 / Vercel Web Interface Guidelines 二轮精修 |
| 后端 API | FastAPI + Pydantic + SQLAlchemy 2.0 async + Alembic | 单 deps.py 两阶段（M1 dev stub → M2 完整 JWT，D-EC） |
| 工作流 | LangGraph 0.6 + AsyncPostgresSaver | 11 节点严格拆分（D-EE）+ checkpoint 续跑 |
| 任务队列 | arq + Redis 7 | `max_tries=1`（D-AY），失败靠用户手动 retry |
| LLM | LiteLLM → 阿里百炼 DashScope（OpenAI 兼容） | 三模型分工，流式 + 重试 + token 记账 |
| 数据库 | PostgreSQL 16 + asyncpg | 10 张表，token_usage CASCADE，DocxJob 状态机 D-CV/D-CU/D-BX 全套 |
| DOCX 导出 | Pandoc + Mermaid CLI + Chromium + Noto CJK + LibreOffice headless | 串行锁 + atomic rename + finalizing 四处 repair |
| 鉴权 | JWT cookie httpOnly + bcrypt + AES-GCM API Key + login throttle | 安全头三件套 / 全局限流 100/min / 改密前 428 |
| 部署 | Docker Compose + supervisord（uvicorn + arq + cron）+ bind mount | 不引入 nginx；备份脚本 `pg_dump -F c` 凌晨 3 点 cron |

---

## 📚 文档导航

| 文档 | 内容 | 给谁看 |
|---|---|---|
| **[`README.md`](README.md)**（本文件） | 项目主页 / 快速开始 / 架构 | 所有人 |
| **[`USER_GUIDE.md`](USER_GUIDE.md)** | 使用指南：从登录到导出 docx 的完整操作流程 | 实际写标书的用户 |
| [`app/README.md`](app/README.md) | 部署运维：docker compose / 升级 / 备份恢复 / 故障排查 | 运维 / 部署者 |
| [`app/REQUIREMENTS.md`](app/REQUIREMENTS.md) | 需求文档：用户故事 + FR / NFR + 验收标准 | 产品 / 评审 |
| [`app/IMPLEMENTATION_SPEC.md`](app/IMPLEMENTATION_SPEC.md) | 实施蓝图：~7100 行，§1-§24 全栈技术决策 + D-A 至 D-EE 决策表 | 后续开发者 / Code review |
| [`app/RUNTIME_TEST_REPORT.md`](app/RUNTIME_TEST_REPORT.md) | 运行时测试报告：5 阶段烟囱测试 + 7 个 R-* runtime bug 修复链 | QA / 验证 |
| [`app/REVIEW_NOTES.md`](app/REVIEW_NOTES.md) | 代码审查记录：4 轮 milestone review 反馈 + 修复对账 | 维护者 |
| [`app/ACCEPTANCE_AUDIT.md`](app/ACCEPTANCE_AUDIT.md) | §23 验收 Checklist：23 条逐条核查 / 跨里程碑契约对账 | 验收方 |

---

## 🛠️ 项目状态

| 阶段 | 状态 |
|---|---|
| M0 CLI 验证 | ✅ |
| M1 后端核心 API（10 表 + 3 类 task + workflow + SSE） | ✅ |
| M2 认证 + 用户管理 + API Key | ✅ |
| M3 DOCX 导出（Mermaid 中文 + 串行锁 + 状态机） | ✅ |
| M4 前端 v1（10 路由 + 8 页面 + shadcn 9 件套） | ✅ |
| M5 部署打包（Docker + supervisord + 备份脚本） | ✅ |
| 4 轮 Code Review + 全栈 §23 验收 | ✅ |
| 14 个 🟡 nit + 38 个 Vercel 指南二轮精修 | ✅ |
| 7 个 runtime bug 修复（passlib/bcrypt / docker / FastAPI 注解 / arq API / SSE 持久化 / 段落空行 / .doc 兼容 / mermaid 白底 / revise patch / 启动 reconciler） | ✅ |
| 真服务器 6 小时压测 + 备份恢复演练 | ⏳ 待用户跑 |

---

## 🔐 安全与合规

- **API Key 真快照**（FR-7.6 / D-C）：用户启动项目时把当前 API Key 加密快照拷到 `Project.encrypted_api_key_snapshot`，后续工作流读这个快照；用户删除 / 修改 ApiKey 不影响在跑的项目
- **AES-GCM** 加密 ApiKey + master_key 校验长度 64 hex
- **改密前 428 拦截** + login throttle 5 次失败锁 5 分钟（IP 维度）
- **CSP / X-Frame-Options / X-Content-Type-Options** 安全头三件套
- **全局限流** 100 req/min/IP
- **`master_key` 一旦丢失** → 所有 API Key 永久不可解密 → 启动横幅打 sha256 前缀供运维与备份比对（R10）

---

## ⚠️ 已知限制

1. 单容器部署，**单实例**，无 HA（内网 10 人共享池场景够用）
2. PostgreSQL 数据卷崩了 → workflow checkpoint 全丢；建议每天 pg_dump（脚本已就绪）
3. LLM 长文本生成 → 偶尔超 60 秒 idle 触发 SSE 重连 → 已通过 SSE 心跳 + DB persisten flush 兜底
4. Mermaid 客户端渲染 → 极少数老语法 LLM 输出可能渲染失败 → 已加 fallback 显示源码 + mermaid.live 一键调试
5. 不支持 .pdf / .ppt 直接抽取（markitdown + libreoffice 不覆盖）→ 用户需先转 .docx

---

## 📝 License

内部使用。本项目所有代码与文档由实施团队完成，含 Co-Authored-By Claude Opus 4.7 (1M context) 标注。
