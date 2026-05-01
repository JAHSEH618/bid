# 投标技术方案生成器 · 实施 Spec v3.4

> **配套文档**:`REQUIREMENTS.md` v0.9(讲"做什么"),本文档讲"怎么做"。
> **版本**:v3.4 (2026-05-02)。基于 v3.3 修了 7 处深层隐患:**workflow task `max_tries=1`** 防 arq 自动重跑绕过名额管理(D-Z) / **slot lease token** 解决"reservation 失效后无 slot 执行"(D-AB) / `ReviewEvent` 移到 worker 入口写避免与执行不一致(D-AC) / DOCX 与 workflow 并发预算分离(D-AA) / 审核端点行锁+状态校验(D-AD) / errors.log 改 JSONL 解决多行字段(D-AE) / 旧文字残留 + REQUIREMENTS 状态机加 queued。
> **历史**:v3 修 18 处;v3-pass2 修 9 处;v3.2 修 8 处;v3.3 修 8 处;v3.4 修 7 处。
> **文档定位**:**实施蓝图**(每段代码是基线,落地时按工程实践再加日志/异常/参数校验)。代码片段做了类型检查级别的正确性,但**不是 100% "复制即跑"**:连接池、错误码细节、Pydantic schema 字段需要在落地时按需补齐。
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
   │ │ │   读 ← DB / EventBus(Redis pub/sub)      │ │
   │ │ │                                           │ │
   │ │ └─ arq worker (独立 Python 进程,共用 .env)│ │
   │ │     ├─ start_workflow_task                  │ │
   │ │     │   └─ LangGraph + AsyncPostgresSaver  │ │
   │ │     │       ├─ extract → outline → ...     │ │
   │ │     │       ├─ outline_review (interrupt)  │ │
   │ │     │       ├─ write_chapter (LLM-2 流式)  │ │
   │ │     │       │   └─→ EventBus → SSE         │ │
   │ │     │       └─ human_review (interrupt)    │ │
   │ │     ├─ resume_review_task (Command(resume))│ │
   │ │     ├─ retry_failed_chapter_task            │ │
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
- **EventBus 实现走 Redis pub/sub(本项目最终选型)**。早期单进程方案曾考虑 asyncio.Queue,但 D-A 把 uvicorn / arq 拆成两进程后跨进程通信不可能用进程内队列;统一改 Redis pub/sub,见 §12。
- LangGraph checkpoint 在 PostgreSQL 持久化,任意进程重启工作流可从 checkpoint 续跑(NFR-2)。

---

## 3. 关键设计决定与 v2 修正项

### 3.1 设计决定(均带 rationale)

| # | 决定 | Rationale |
|---|---|---|
| **D-A** | uvicorn 与 arq 走 **两个进程**,supervisord 编排 | 单进程 asyncio.create_task 跑 arq 一旦 worker 崩了拖垮 HTTP;分进程隔离更稳 |
| **D-B** | EventBus 用 **Redis pub/sub** 跨进程,FastAPI 端订阅频道转 SSE | 因为 D-A 拆了进程,asyncio.Queue 不能跨进程 |
| **D-C** | API Key **启动时快照到 `Project.encrypted_api_key_snapshot`**(BLOB,AES-GCM 加密),运行时从该字段取,**不**进 LangGraph state/checkpoint | v2 写"运行时从 ApiKey 表取"是错的——用户重置 Key 会污染已启动项目,违反 FR-7.6。**真快照**:`/start` 端点拷贝当时 ApiKey.encrypted_key 到 Project,工作流后续都用这个,与用户后续行为完全解耦 |
| **D-D** | LangGraph 节点封装 **整个流式收集** 在 `asyncio.timeout(600)` 内 | FR-3.10 的 10 分钟必须包住完整流式过程,不是单次 await |
| **D-E** | 章节渲染前端用 **react-markdown**(不用 Tiptap) | 审核场景只读 + 流式 append 字符串更简单;Tiptap 是给可编辑场景的过度选型 |
| **D-F** | `must_change_password` 用 **FastAPI dependency** 拦截而非 starlette middleware | 中间件无法拿 user 上下文;dependency 自然取到当前用户 |
| **D-G** | Health check **只查 db / redis**,LLM 连通单独 `/api/me/api-key/test` | 健康检查应快、应只查内部依赖,不应被外网拖慢 |
| **D-H** | DOCX 串行用 **module-level `asyncio.Lock` + Redis lock 双保险**;Redis 端用 `redis.asyncio.Lock`(token + Lua compare-and-delete) | arq 可能跨多个 worker 进程(未来扩容);Redis lock 保证 chromium 同时只一个;Lua CAS 防止误删别人的锁 |
| **D-I** | LangGraph 三类任务**显式拆分**:`start_workflow_task`(全新)/`resume_review_task`(`Command(resume=...)` 注入审核决策)/`retry_failed_chapter_task`(重置 chapter + state 后续跑) | v2 把 review 与 retry 混用 graph.astream(None) 不可靠;现按"动作类型 → 任务"一一对应,从同一个 thread_id 的不同语义状态恢复 |
| **D-J** | 工作流节点状态变化时,**通过 hooks 同步到 DB Chapter 表**,SSE 事件由 EventBus 异步推 | DB 是真相源;EventBus 失败不影响数据正确性 |
| **D-K** | **提纲确认是工作流内的真 interrupt 节点**,不是 graph 外的 HTTP 调度 | v2 的 graph 直冲 pick_chapter,P4 提纲编辑无暂停点;新增 `outline_review` 节点用 `interrupt()`,`/confirm-outline` 端点触发 `Command(resume={chapters: edited})` 续跑 |
| **D-L** | DOCX 缓存路径**固定为 `{project_dir}/proposal.docx`**;下载文件名通过 `FileResponse(filename=...)` 动态生成 | 磁盘文件名带时间戳每天变 → 缓存命中无法判断 → 反复重生成。固定文件名缓存,展示名动态 |
| **D-M** | DOCX 任务**先在 DB 落 `DocxJob(status=pending, arq_job_id=NULL)` 再 enqueue**,enqueue 后立刻 UPDATE arq_job_id | v2 先 enqueue 再 INSERT 有竞态:worker 拿到 job 时 DB 行可能还没写入。先 INSERT flush 拿 id,再用 id 入队 |
| **D-N** | Mermaid 扫描用 **`re.finditer` + 反向 span 替换**,正则容忍 CRLF / 行尾空格 / `~~~mermaid` 围栏 | v2 用 `str.replace` 在重复 mermaid 块时会覆盖第一处之外的全部;反向替换不影响后续 span |
| **D-O** | DB Migration 与服务启动**用 entrypoint 串行化**:容器入口先 `alembic upgrade head` 通过才 `exec supervisord` | supervisord priority 不等于"等到上一个完成";migrate 与 uvicorn 同时启可能导致表不存在时 HTTP 已开始接 |
| **D-P** ⚠️ 由 D-T 取代 | 并发项目上限**双层防护**:业务层 Redis 跟踪 + arq `WorkerSettings.max_jobs=N`(兜底) | 业务侧需要"超限就 queued"的语义。最终实现见 D-T(SET + alive TTL,**非计数器**) |
| **D-Q** | 登录失败锁定**用 Redis 计数 + 锁 key**,不依赖 slowapi 的请求级限流 | FR-6.7 要求"5 次失败后锁该 IP 5 分钟",slowapi 是"匀速速率",语义不一致;改用 `INCR + EXPIRE` 双 key |
| **D-R** | `.env` **单文件方案**:`gen-secrets.sh` 从 `.env.example` seed + sed 替换占位符,生成最终唯一 `.env`,compose 直接读 | docker compose `${VAR}` 插值只读 compose 项目根 `.env` / 宿主 env,**不**读 service 的 `env_file:` 列表;两文件方案中 postgres `${POSTGRES_PASSWORD}` 会插到占位符,起不来 |
| **D-S** | DocxJob.arq_job_id `nullable=true` + **partial unique index** `WHERE arq_job_id IS NOT NULL`;同时 `(project_id) WHERE status IN ('pending','rendering_mermaid','pandoc')` 也 partial unique | 入队前先 INSERT 拿 id 必须支持空 arq_job_id;并发同项目两次 POST docx 应被 DB 阻断,而不是靠应用层抢锁 |
| **D-T** | 并发名额用 **Redis SET + 每项目 alive TTL key**,不用计数器;**worker 启动时 reconcile**;唤醒由幂等 `wake_queued_projects(arq_pool)` 函数,不让"占不到名额的任务"留在 arq 里重试。**人工等待不占名额**——task 因 interrupt 退出时 release;`/review` `/confirm-outline` `/retry` 在 enqueue 前重新 `try_acquire`,占不到 → 503 + Retry-After:60 | 计数器在 worker 崩溃时会泄漏正数;SET 与 alive key TTL 配合可识别僵尸条目;worker 进程结束后 heartbeat 必停 → "interrupt 期间持续占名额"物理上不可行;改成"task 周期 = slot 周期"语义干净,且 awaiting_review 时其他项目可以跑 |
| **D-U** | DB commit 后再 enqueue 走**补偿动作**:enqueue 失败 → 回退 DB status + release slot + 503。`wake_queued_projects` 同样:enqueue 失败 → release + 把项目改回 queued | outbox 表对内部 10 用户工具 over-engineering;补偿动作 + reconcile 兜底已足够;reconcile 在 worker 启动时也会清理"无 alive key 但状态 running"的僵尸项目 |
| **D-V** | Redis 用 **`noeviction` 内存策略**,不用 `allkeys-lru` | 同一 Redis 同时承载 arq 队列、active set、login lock、event pub/sub、limiter 计数。LRU 策略下,内存压力大时 Redis 会**默默驱逐任意 key**,可能让 arq 任务、并发名额、登录锁全部消失,触发难定位的 silent 故障。`noeviction` 让写入在内存满时显式失败,我们在监控 / OOM 风险下能立刻发现 |
| **D-W** | DSN(`DATABASE_URL` / `LANGGRAPH_DSN`)由 **`config.py` 从 `POSTGRES_USER/PASSWORD/HOST/PORT/DB` 字段拼装**,不在 `.env` 写带 `${VAR}` 的派生值 | docker compose 的 `env_file:` **不**对值做变量展开;容器内 `pydantic-settings` 读 OS env 也不展开。`.env` 里 `DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@...` 实际进容器是字面值。改在 `config.py` 用 property 拼,既避免展开问题,又支持密码里含 `@/&` 等需要 URL-quote 的字符 |
| **D-X** | 项目级错误日志写到 **`{project_dir}/errors.log`**(NFR-5 要求),与 stdout structlog 并存;**异常路径写完整 `traceback.format_exc()` 而不是 `repr(e)`** | 用户排查"为什么这个项目挂了"时,需要看项目目录里的错误而不是从 docker logs 海量 stdout 里搜索;NFR-5 明确要"完整堆栈" |
| **D-Y** | ALIVE_KEY **双 TTL**:API try_acquire 设 RESERVE_TTL=300s(预留 enqueue→worker 启动的延迟窗口);task 进入 heartbeat 后续租到 ALIVE_TTL=60s | v3.2 用单 60s TTL,arq 排队 / worker 重启延迟时 ALIVE_KEY 过期 → reconcile 误清 ACTIVE_SET → 并发统计失真。两段 TTL 让"reservation"与"alive"语义分开 |
| **D-Z** | workflow 三类 task **`max_tries=1`**;DOCX task `max_tries=2` | LLM 失败后 task 抛异常,arq 默认会自动重试整个 task,这会:① 绕过 API 端 `try_acquire`(违反 D-T 名额管理);② 与 FR-3.9/FR-4.7 的"用户手动 /retry"语义冲突;③ 浪费 LLM 调用。崩溃恢复改靠 LangGraph checkpoint(用户下次 /retry 时从最近成功节点续跑)。DOCX 失败多是临时(pandoc/mermaid 偶发),自动重试 1 次有意义 |
| **D-AA** | `WorkerSettings.max_jobs = max_concurrent_projects + 2`,给 DOCX task 留余量;DOCX Redis 锁 `blocking_timeout` 缩短到 120s 防止"等锁的 DOCX 占满 worker job 槽" | DOCX 串行锁让多个 DOCX 任务在 arq worker 里阻塞等待,如果 max_jobs 等于项目并发上限,等锁的 DOCX 把 worker 槽全占,新 workflow task 入队后排在 arq 队列里饿死 |
| **D-AB** | slot **lease token**:`try_acquire` 返回 uuid token 存到 ALIVE_KEY 值;task 入口 `ensure_project_slot(token)` 校验;`heartbeat`/`release` 用 Lua CAS 仅当 token 匹配才操作 | 仅靠 RESERVE_TTL 不能消除时序竞态:worker 阻塞 / 排队 > 5 分钟时 ALIVE_KEY 过期 → reconcile 把 ACTIVE_SET 清掉 → task 后续启动时**不知道**自己已被踢,继续跑导致并发超限。token 让 task 能精确判断"我还持有 slot 吗",失败可重新 acquire 或退出 |
| **D-AC** | `ReviewEvent` 在 **worker 入口**写,不在 API 端写 | v3.3 把 ReviewEvent 写在 enqueue 后,如果 enqueue 成功但 commit 失败,动作执行了但事件没写。改在 worker 入口写,与 graph 真正执行同生同灭:worker 拿到 task 之前不写,worker 执行前必写,失败时 ReviewEvent 也保留(记录"用户做过这个动作")。代价是 enqueue 后 worker 拿到 task 之前的极小窗口里 DB 看不到 ReviewEvent,但这是正确的——审计应该反映"实际发生过"而不是"被请求过" |
| **D-AD** | API 端审核/重试加 **`SELECT ... FOR UPDATE` 行锁** + 状态校验 | 防止过期页面 / 双击 / 多用户同时审同一章节导致重复提交;状态校验确保只有 `awaiting_review` 章节能被 review、`failed` 章节能被 retry |
| **D-AE** | errors.log 用 **JSONL 格式**,traceback 作为单行 JSON 字符串字段 | v3.3 设计的 key=val 平文本假设"每行 < PIPE_BUF 4KB 不交错",但 traceback 多行长字段打破假设。JSONL 把多行字段编码成单行 JSON,单次 write 完成,跨进程 append 不交错 |

### 3.2 v2 → v3 修正项一览

| # | 原 v2 缺陷 | v3 正确做法 | 章节 |
|---|---|---|---|
| 1 | API Key 运行时反查 ApiKey 表(伪快照) | Project 加 `encrypted_api_key_snapshot` BLOB,`/start` 拷贝快照,运行时只从该字段取 | §8 / §9 / §10.1 / §11.2 / §15.1 |
| 2 | parse_outline → 直冲 pick_chapter,无 P4 暂停 | 新增 `outline_review` interrupt 节点 + `/confirm-outline` 端点 | §10.2 / §10.7 / §15.1 |
| 3 | review/retry 都模糊地走 `graph.astream(None)` | 拆 `start_workflow_task` / `resume_review_task` / `retry_failed_chapter_task` 三个 arq 任务 | §10.5 |
| 4 | retry 仅把 status='failed' → 'pending',retry_count 不重置 | retry 事务:status='pending'、retry_count=0、last_error=NULL、本轮 ChapterVersion 标 abandoned、记 ReviewEvent | §10.5 / §15.2 |
| 5 | `new_retry >= max_retry` off-by-one | 改 `>`;字段语义补"配置 N 表示允许 N 次重写,第 N+1 次自动 skip" | §10.4 / §11 |
| 6 | docx 输出 `{name}_{date}.docx` 但下载查 `proposal.docx` | 缓存固定 `proposal.docx`,FileResponse(filename=动态名) | §13.1 / §15.3 |
| 7 | 先 enqueue 再 INSERT DocxJob,有竞态 | 先 INSERT flush 拿 id → enqueue → UPDATE arq_job_id | §15.3 |
| 8 | `_redis_lock` 是 async generator 缺 `@asynccontextmanager`;释放直接 DEL | 用 `redis.asyncio.Lock(blocking_timeout)` + Lua CAS;或手写 token+Lua | §13.1 |
| 9 | mermaid 正则 `\nmermaid\n...\n` 太死;`str.replace` 同代码块重复时错位 | `re.finditer` + 反向替换 + 兼容 CRLF / 行尾空格 / `~~~` 围栏 | §13.1 |
| 10 | Dockerfile 没装 postgresql-client;compose 用 named volume | Dockerfile 装 `postgresql-client-16`;compose 改宿主机 bind mount | §17.1 / §17.3 |
| 11 | supervisord 用 priority 编排 migrate→服务,不可靠 | entrypoint 同步 alembic upgrade head 再 exec supervisord | §17.1 / §17.2 |
| 12 | 并发上限只在 .env 写,代码没用 | Redis SET(`ACTIVE_SET`)+ 每项目 alive TTL + `WorkerSettings.max_jobs` 兜底 + queued 状态(D-T,非计数器) | §10.7 / §15.1 / §17.2 |
| 13 | token_usage.project_id `ON DELETE SET NULL` 与需求"删除项目连带 TokenUsage"冲突 | 改 `CASCADE`;DELETE API 同步删磁盘目录 | §8 / §9 / §15.1 |
| 14 | login 用 `@limiter.limit("5/minute")` 限的是请求频率,不是失败次数 | Redis: `login_fail:{ip}` INCR + `login_lock:{ip}` SET EX 300 | §14.3 / §14.5 |
| 15 | 缺 security headers / 全局 limiter / 上传配额 | `SecurityHeadersMiddleware` + `default_limits=["100/minute"]` 真注册 + 上传时聚合 Documents.file_size 校验 | §14.6 / §15.1 |
| 16 | `gen-secrets.sh > .env; cat .env.example >> .env` 后写覆盖密钥 | gen-secrets.sh 自动 `cp .env.example .env` + sed 替换占位符,生成单一最终 `.env`(D-R)。compose 不再用 env_file 列表(`${VAR}` 插值不读 service env_file) | §6.2 / §7.2 / §17.3 |
| 17 | 需求与 Spec health 口径不一致 | 需求 v0.6 已收窄;Spec 同步加 `/api/me/api-key/test` 端点 | §15.5 |
| 18 | `call_llm_json` "略"、`generate_docx_task` "..." 占位 | 全部补完整代码 | §11.1 / §13.3 |

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
  "psycopg[binary]>=3.2,<4",     # 给 Alembic 同步 + langgraph-checkpoint-postgres 用
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

# ─── 数据库(组件字段;DSN 由 config.py 拼装,见 D-W)──────
# 注意:不再写 DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@...
# 因为 docker compose env_file 不展开 ${VAR},容器内会拿到字面值。
POSTGRES_USER=bid_app
POSTGRES_PASSWORD=__GENERATE_ME__
POSTGRES_DB=bid_app
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

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
GLOBAL_RATE_LIMIT=100/minute
LOGIN_FAIL_MAX_PER_MINUTE=5
LOGIN_LOCK_SECONDS=300

# ─── 路径(容器内)────────────────────────────
PROJECTS_DIR=/var/lib/bid-app/projects
BACKUPS_DIR=/var/lib/bid-app/backups
TEMPLATES_DIR=/app/backend/templates

# ─── 日志 ──────────────────────────────────────
LOG_LEVEL=INFO
```

### 6.2 密钥生成与 .env 组合(D-R 路径)

⚠️ **关键背景**:docker compose 在解析 `compose.yml` 时,对 `${POSTGRES_PASSWORD}` 这类**插值**只读取 compose 项目根目录的 `.env`(或 `--env-file`)和宿主 shell 环境,**不**读 service 的 `env_file:` 列表。所以"app `env_file: [.env, .env.secrets]` 让 postgres 拿到 secrets"在技术上是错的——postgres 容器的 `${POSTGRES_PASSWORD}` 仍然会被替换成 `.env` 里的占位符。

**正确做法**:`gen-secrets.sh` 直接在唯一一份 `.env` 上做 in-place 替换,生成最终可用文件,compose 只读这一份。

`scripts/gen-secrets.sh`(D-R 修正版):

```bash
#!/usr/bin/env bash
# 用法: ./scripts/gen-secrets.sh        # 默认 ./.env
#       ./scripts/gen-secrets.sh foo.env
#
# 行为:从 .env.example seed 出 .env,然后把所有 __GENERATE_ME__ / __64_HEX_CHARS__
# 占位符替换为真随机密钥。已经填好真值的字段不动。
set -euo pipefail

EXAMPLE="${EXAMPLE_FILE:-.env.example}"
OUT="${1:-.env}"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "❌ $EXAMPLE 不存在,先 cd 到 app/ 再跑" >&2; exit 1
fi
if [[ -f "$OUT" ]]; then
  echo "❌ $OUT 已存在,拒绝覆盖。要重置:mv $OUT $OUT.bak && $0 $OUT" >&2; exit 1
fi

umask 077
cp "$EXAMPLE" "$OUT"

# 生成密钥(用 Python 而不是 openssl,避免不同发行版差异)
gen_hex() { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }
gen_url() { python3 -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"; }

MASTER_KEY="$(gen_hex 32)"
JWT_SECRET="$(gen_hex 32)"
PG_PASSWORD="$(gen_url 24)"

# 注意 sed 分隔符避开 base64 / hex 字符
sed -i.bak \
  -e "s|^BID_APP_MASTER_KEY=.*|BID_APP_MASTER_KEY=${MASTER_KEY}|" \
  -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASSWORD}|" \
  "$OUT"
rm -f "${OUT}.bak"
chmod 600 "$OUT"

# 自检:不能仍残留 __GENERATE_ME__ / __64_HEX_CHARS__
if grep -E "__GENERATE_ME__|__64_HEX_CHARS__" "$OUT" >/dev/null; then
  echo "❌ $OUT 仍有未替换占位符,请检查 .env.example 字段名" >&2
  grep -nE "__GENERATE_ME__|__64_HEX_CHARS__" "$OUT" >&2
  exit 2
fi
echo "✅ 已生成 $OUT (mode 600)"
```

**首次部署流程**:

```bash
cd app
./scripts/gen-secrets.sh                  # 自动 cp .env.example .env + 替换占位符
# 此时 .env 已是最终版本,docker compose 直接读
docker compose up -d
```

`.gitignore` 必须包含:`.env`、`.env.local`、`.env.*.local`、`.env.bak`(防止 sed 备份意外入库)。`.env.example` 入库,`.env` 不入库。

### 6.3 启动校验

`config.py` 用 `pydantic-settings` 强校验三个必须密钥的格式:

- `BID_APP_MASTER_KEY` 必须是 64 位 hex(32 字节)
- `JWT_SECRET` 必须是 64 位 hex
- `POSTGRES_PASSWORD` 必须非空且不等于 `__GENERATE_ME__`

不符合直接 `sys.exit(1)`,在 docker logs 打出明确错误。

```python
# bid_app/config.py 关键片段
import re, sys
from urllib.parse import quote
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore")

    bid_app_master_key: str
    jwt_secret: str

    # 组件字段(D-W)
    postgres_user: str
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str

    redis_url: str = "redis://redis:6379/0"
    # ... 其余字段省略

    @field_validator("bid_app_master_key", "jwt_secret")
    @classmethod
    def _hex64(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", v):
            raise ValueError("must be 64 hex chars (run scripts/gen-secrets.sh)")
        return v.lower()

    @field_validator("postgres_password")
    @classmethod
    def _not_placeholder(cls, v: str) -> str:
        if not v or v == "__GENERATE_ME__":
            raise ValueError("POSTGRES_PASSWORD must be set (run scripts/gen-secrets.sh)")
        return v

    @property
    def database_url(self) -> str:
        """SQLAlchemy 异步引擎用(asyncpg)。密码 URL-quote 防 @/&/# 等特殊字符。"""
        pwd = quote(self.postgres_password, safe="")
        return (f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

    @property
    def langgraph_dsn(self) -> str:
        """langgraph-checkpoint-postgres 用(psycopg3,纯 DSN 不带 SQLAlchemy 前缀)。"""
        pwd = quote(self.postgres_password, safe="")
        return (f"postgresql://{self.postgres_user}:{pwd}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")


try:
    settings = Settings()
except Exception as e:
    print(f"❌ Config validation failed: {e}", file=sys.stderr)
    sys.exit(1)
```

> ⚠️ 不要再写 `DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@...` 进 `.env`。docker compose 把 `env_file:` 内容**逐字**塞进容器环境(不展开 `${VAR}`),pydantic-settings 读 OS env 也不展开,结果容器拿到字面值 `${POSTGRES_PASSWORD}` 解析失败。让 config.py 自己拼是最稳的方案。

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

# 1. 配置文件:gen-secrets.sh 自动从 .env.example seed + sed 替换占位符
./scripts/gen-secrets.sh         # 生成最终 .env(单一文件)

# 2. 起 db + redis
docker compose -f docker-compose.dev.yml up -d

# 3. 后端
cd backend
uv sync --all-extras
# pydantic-settings 自动读 ../.env(model_config 已配 env_file)
uv run alembic upgrade head

# 终端 A:HTTP
uv run uvicorn bid_app.main:app --reload --port 12123 --host 127.0.0.1

# 终端 B:arq worker(独立进程,与 HTTP 并行)
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
from datetime import datetime
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
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`models/api_key.py`:

```python
from datetime import datetime
from sqlalchemy import LargeBinary, ForeignKey, String, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="dashscope")
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)  # AES-GCM:nonce(12)+ciphertext
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`models/project.py`:

```python
from sqlalchemy import String, ForeignKey, Index, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="init")
    # init | extracting | outlining | outline_ready | queued | running | awaiting_review
    # | done | failed | aborted
    # ⚠️ queued 表示项目已 /start 但全局并发上限已满,排队等位(D-P)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    api_key_owner: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
    )  # 启动者引用,审计/UI 展示用;真正运行用的是下面的快照字段
    encrypted_api_key_snapshot: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True,
    )  # ⭐ D-C 真快照:启动时拷贝 ApiKey.encrypted_key 到这里,工作流后续都从这里读。
       # 用户后续重置 / 删除 ApiKey 不影响本项目(FR-7.6)。
    dir_path: Mapped[str] = mapped_column(String(512))
    pages_per_chapter: Mapped[int] = mapped_column(default=3)
    max_retry_per_chapter: Mapped[int] = mapped_column(default=3)
    # max_retry_per_chapter 语义:允许 N 次重写;第 N+1 次"不通过"自动 skip(FR-4.2,D-?)
    # 例如 max=3 表示原稿 + 最多 3 次重写,共 4 个版本上限
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
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    langgraph_thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
from sqlalchemy import String, ForeignKey, Text, Integer, Boolean, UniqueConstraint
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
    abandoned: Mapped[bool] = mapped_column(Boolean, default=False)
    # ⭐ FR-4.7:章节 retry_failed 时,本轮所有未审版本标 abandoned=true,
    #   保留历史不删除,但全文整合 / 列表查询默认过滤掉。
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
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True,
    )  # ⭐ FR-1.6:删除项目连带删 TokenUsage(v2 是 SET NULL,与需求矛盾)
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
    # 表级唯一索引(partial)在 migration 里建,见 §9。
    # - uq_docx_jobs_arq_job_id  : (arq_job_id) WHERE arq_job_id IS NOT NULL
    # - uq_docx_jobs_project_inflight : (project_id) WHERE status IN
    #   ('pending','rendering_mermaid','pandoc') —— D-S 同项目同时只允许 1 个 in-flight

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    arq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ⭐ D-S:nullable=True;入队前先 INSERT 占位拿主键 id,enqueue 后再 UPDATE arq_job_id。
    # 唯一性走 partial unique index,允许多条 NULL。
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|rendering_mermaid|pandoc|done|failed
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

> 注:`DocxJob` 模块顶部需 `from datetime import datetime`。其他模型(User/ApiKey/Run)同样。

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
import os
from urllib.parse import quote

from bid_app.models import Base
target_metadata = Base.metadata


def get_url() -> str:
    """与 config.py 的 settings.database_url 等价,但 alembic 用同步驱动 psycopg3。
    不读 DATABASE_URL 环境变量(已不再设),从组件字段拼。"""
    user = os.environ["POSTGRES_USER"]
    pwd = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"


config.set_main_option("sqlalchemy.url", get_url())
```

> ⚠️ 不要替换为裸 `postgresql://`,SQLAlchemy 默认走 psycopg2,而我们没装 psycopg2;migration 会启动即失败。
> `langgraph-checkpoint-postgres` 用的 DSN 由 `settings.langgraph_dsn` 拼装,内部用 psycopg3。

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
        sa.Column("encrypted_api_key_snapshot", sa.LargeBinary),  # D-C 真快照
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
        sa.Column("abandoned", sa.Boolean, nullable=False, server_default="false"),
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

    # token_usage(project_id 改 CASCADE,与 FR-1.6 对齐)
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_token_usage_user_month", "token_usage", ["user_id", "created_at"])
    op.create_index("ix_token_usage_project", "token_usage", ["project_id"])

    # docx_jobs(D-S:arq_job_id nullable + partial unique;同项目 in-flight 唯一)
    op.create_table(
        "docx_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("arq_job_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(4000)),
        sa.Column("output_path", sa.String(512)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_docx_jobs_arq_job_id", "docx_jobs", ["arq_job_id"],
        unique=True,
        postgresql_where=sa.text("arq_job_id IS NOT NULL"),
    )
    op.create_index(
        "uq_docx_jobs_project_inflight", "docx_jobs", ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','rendering_mermaid','pandoc')"),
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
    project_id: int            # ⭐ DB 查询入口
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

    # === Human Review 临时载体(由 Command(resume=...) 注入)===
    _review_decision: str   # approve | revise | skip
    _review_feedback: str

    # === Outline 编辑临时载体(P4 提纲确认,D-K)===
    _outline_confirmed_chapters: list[dict] | None
    # 由 /confirm-outline 端点通过 Command(resume={...}) 注入。
    # 若为 None / [] 走"自动确认",直接用 LLM-1 生成的 chapters 进入循环。

    # === 输出 ===
    final_proposal: str | None
```

> **⚠️ 不放 `api_key`**(D-C):防止被 PostgresSaver 落库。
> 运行时通过 `project_id` → `Project.encrypted_api_key_snapshot` → AES-GCM 解密。
> v2 写"通过 `Project.api_key_owner` 反查 ApiKey"是错的——那是用户级实时 Key,会让用户重置 Key 污染已启动项目。**真快照**字段只在 `/start` 时被写入,工作流读它而不是反查。

### 10.2 Graph(`workflow/graph.py`)

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from .state import WorkflowState
from .nodes import (
    extract_documents, generate_outline, parse_outline,
    outline_review, pick_chapter, write_chapter, gen_visuals, merge_chapter,
    human_review, update_state, assemble,
)

def build_graph(checkpointer: AsyncPostgresSaver):
    g = StateGraph(WorkflowState)

    g.add_node("extract_documents", extract_documents.run)
    g.add_node("generate_outline", generate_outline.run)
    g.add_node("parse_outline", parse_outline.run)
    g.add_node("outline_review", outline_review.run)   # ⭐ 新增 P4 暂停点(D-K)
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
    g.add_edge("parse_outline", "outline_review")
    g.add_edge("outline_review", "pick_chapter")
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

> **interrupt 是节点内调用**:`outline_review` 与 `human_review` 节点内部调用 `interrupt(...)`,LangGraph 会暂停在该节点;再次执行 `graph.ainvoke(Command(resume=...), config)` 时该节点会重启并把 resume 值作为返回。两个 interrupt 节点的 resume payload 形状不同(见 §10.7、§10.4)。

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
    # ⭐ 语义:max_retry_per_chapter=N 表示**允许 N 次重写**;
    # 第 N+1 次"不通过"才强制 skip。所以判定用 > 而不是 >=。
    # 例如 N=3 时 retry_count 序列 0(原稿审)→1→2→3(允许);
    # 第 4 次"不通过"时 new_retry=4 > 3 → skip。
    if new_retry > state["max_retry_per_chapter"]:
        # 超限 → 强制 skip
        skip_marker = (f"<!-- ⚠️ 章节《{chapter['title']}》重写超限"
                       f"({state['max_retry_per_chapter']} 次)被强制累积 -->\n{pending_md}")
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

### 10.5 三类 arq 任务清晰拆分(D-I)

v2 把 review/retry 都模糊地走"重新 enqueue + astream(None)",对 LangGraph interrupt 与异常恢复语义混淆。v3 按**动作类型**显式拆三个任务,每个任务对应明确的 LangGraph 入口:

| 任务 | 触发 | LangGraph 入口 | 说明 |
|---|---|---|---|
| `start_workflow_task` | `/start` 端点 | `graph.ainvoke(initial_state, config)` | 全新 thread_id,从 `extract_documents` 跑;遇到 outline_review 或 human_review interrupt 自然停 |
| `resume_review_task` | `/review` 或 `/confirm-outline` 端点 | `graph.ainvoke(Command(resume=...), config)` | 带审核决策(章节)或编辑后提纲,LangGraph 自动从 interrupt 节点重启并填值 |
| `retry_failed_chapter_task` | `/chapters/{idx}/retry` 端点 | DB 重置 + `graph.ainvoke(None, config)` | failed 是节点抛异常停的,checkpoint 在该节点之前的 update_state(或起点),`None` 让它从 checkpoint 续跑 |

**任务实现细节**(完整代码移到 §10.7 与并发控制一起,因为名额管理与任务生命周期紧耦合):见 §10.7 末尾"worker/tasks.py 的三类任务"小节。

**关键约定**:
- `/start` 端点决定占名额还是 queued;**任务自身不再 try_acquire**(避免任务空跑+arq retry 浪费)
- 每个任务都用 `async with project_heartbeat(...)` 包住 graph 执行,定期续租 alive TTL
- 任务结束 finally 块**无条件 release**(D-T 修正:人工等待不占名额);下一次 resume/retry 由对应 API 端点重新 `try_acquire`

**为什么这样拆比 v2 强**:
- LangGraph 文档明确:从 interrupt 恢复用 `Command(resume=value)` 注入值,不是 `astream(None)`
- start vs resume vs retry 三种语义在 checkpoint 上的位置完全不同;混用会导致 resume 的 decision 注入不进 graph、retry 时旧 retry_count 残留等隐蔽 bug
- 三个任务名称在 `WorkerSettings.functions` 显式注册,任何调用方一目了然

### 10.6 Outline 确认节点(`workflow/nodes/outline_review.py`)

```python
"""P4 提纲确认节点(D-K)。
parse_outline 已经把 LLM-1 输出落到 state.chapters。
此节点:
1. 把 chapters 落 DB(给 P4 渲染)
2. ⭐ 写 Project.status = 'outline_ready'(让 /confirm-outline 端点能通过状态校验)
3. publish 'outline_ready' SSE
4. interrupt 等用户编辑;resume 后再写 Project.status = 'running'

resume payload 形状:
  {"kind": "outline_confirm",
   "chapters": [...]  # 用户编辑后的章节;为空/None 表示自动确认}
"""
from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_outline_to_db, sync_project_status


async def run(state: WorkflowState) -> dict:
    pid = state["project_id"]

    # 1+2. 落 DB,project 进入 outline_ready
    await sync_outline_to_db(state["run_id"], state["chapters"])
    await sync_project_status(pid, "outline_ready")

    # 3. SSE 通知前端拉提纲
    await publish_event(pid, "outline_ready", chapters=state["chapters"])

    # 4. interrupt 暂停;后续由 /confirm-outline → resume_review_task 注入
    payload = interrupt({"kind": "outline_confirm",
                         "current_chapters": state["chapters"]})

    # resume 后:Project 回到 running,准备进章节循环
    await sync_project_status(pid, "running")

    edited = (payload or {}).get("chapters")
    if edited:
        await sync_outline_to_db(state["run_id"], edited, replace=True)
        return {"chapters": edited, "current_index": 0,
                "_outline_confirmed_chapters": edited}

    return {"current_index": 0,
            "_outline_confirmed_chapters": state["chapters"]}
```

### 10.6b Human review 节点(`workflow/nodes/human_review.py`)

```python
"""P5 章节审核 interrupt 节点。
触发前章节正文已由 merge_chapter 节点放入 state['_pending_chapter_text'],
DB Chapter 状态由 write_chapter 节点写成 'awaiting_review'。
此节点只做项目级状态切换 + SSE 通知 + interrupt。"""
from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status


async def run(state: WorkflowState) -> dict:
    pid = state["project_id"]
    idx = state["current_index"]

    await sync_project_status(pid, "awaiting_review")
    await publish_event(pid, "awaiting_review", chapter_index=idx,
                        chapter_text=state.get("_pending_chapter_text"))

    payload = interrupt({"kind": "chapter_review", "chapter_index": idx})

    # resume 后:project 回 running;decision/feedback 写进 state 给 update_state
    await sync_project_status(pid, "running")
    return {
        "_review_decision": (payload or {}).get("decision", "approve"),
        "_review_feedback": (payload or {}).get("feedback", ""),
    }
```

### 10.6c Assemble 节点(`workflow/nodes/assemble.py`)

```python
"""全文整合 + 持久化输出(v10 §4.6)。
所有章节 finalized 后跑;同步:
- 写 {project_dir}/proposal.md(给 docx 任务读)
- Run.finished_at + status='done'
- Project.status='done'
- SSE 'proposal_ready'"""
import sqlalchemy as sa
from datetime import datetime, timezone
from pathlib import Path

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status
from ...db import session_factory
from ...workflow.prompts.assemble import build_proposal


async def run(state: WorkflowState) -> dict:
    final_md = build_proposal(
        chapters=state["chapters"],
        finalized_chapters=state["finalized_chapters"],
    )

    async with session_factory() as s:
        # 取 project_dir
        prj = await s.execute(sa.text(
            "SELECT dir_path FROM projects WHERE id=:p"),
            {"p": state["project_id"]},
        )
        project_dir = Path(prj.scalar_one())

        # Run 落 done
        await s.execute(sa.text(
            "UPDATE runs SET finished_at=:t, status='done' WHERE id=:r"
        ), {"r": state["run_id"], "t": datetime.now(timezone.utc)})
        await s.commit()

    # 写 proposal.md(给 generate_docx_task 读)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "proposal.md").write_text(final_md, encoding="utf-8")

    await sync_project_status(state["project_id"], "done")
    await publish_event(state["project_id"], "proposal_ready")

    return {"final_proposal": final_md}
```

### 10.6d sync helper 增补(`workflow/sync.py`)

```python
async def sync_project_status(project_id: int, status: str) -> None:
    async with session_factory() as s:
        await s.execute(
            sa.text("UPDATE projects SET status=:s WHERE id=:p"),
            {"s": status, "p": project_id},
        )
        await s.commit()


async def sync_outline_to_db(run_id: int, chapters: list[dict],
                             *, replace: bool = False) -> None:
    """把 chapters 数组落到 chapters 表。replace=True 时先清空再写(用户编辑后用)。

    注:用 ORM insert 而不是裸 sa.text + JSON 字符串绑定,
    SQLAlchemy 自己会把 list 序列化成 jsonb,避免 sa.text 时的类型推断歧义。"""
    from ..models import Chapter
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_factory() as s:
        async with s.begin():
            if replace:
                await s.execute(
                    sa.text("DELETE FROM chapters WHERE run_id=:r"),
                    {"r": run_id},
                )
            for i, c in enumerate(chapters):
                stmt = pg_insert(Chapter).values(
                    run_id=run_id,
                    index=i,
                    title=c["title"],
                    summary=c.get("summary"),
                    key_points=c.get("key_points", []),   # SQLAlchemy 把 list → JSON
                    target_pages=c.get("target_pages", 3),
                ).on_conflict_do_update(
                    index_elements=["run_id", "index"],
                    set_={
                        "title": sa.text("EXCLUDED.title"),
                        "summary": sa.text("EXCLUDED.summary"),
                        "key_points": sa.text("EXCLUDED.key_points"),
                        "target_pages": sa.text("EXCLUDED.target_pages"),
                    },
                )
                await s.execute(stmt)
```

### 10.7 并发名额服务(`services/concurrency.py`,D-T 重写)

修复 v3-pass1 的几个隐患:
- 计数器在 worker 崩溃时漂移 → 改成 **Redis SET**(`SADD/SREM`),配合每项目 alive TTL key 检测僵尸
- `release_project_slot` 内嵌唤醒逻辑容易重复 enqueue → 抽出 `wake_queued_projects(arq_pool)`,**幂等**(SETNX 排他锁)
- `event_bus.get_arq_pool()` 不存在 → arq_pool 由 worker ctx 持有,作为参数传入
- "占不到名额"应**正常 return**,不抛异常让 arq retry,否则反复消耗资源

```python
"""项目并发上限(D-P / D-T / D-Y 双 TTL 修正):
- ACTIVE_SET   : Redis SET 持当前活跃 project_id 集合,基数即占用名额
- ALIVE_KEY    : 每项目一个 TTL key,有两种 TTL 阶段:
                 ├─ API try_acquire 时设 RESERVE_TTL=300s(reservation,
                 │   覆盖 arq 排队/worker 重启的延迟窗口)
                 └─ task 进入 heartbeat 上下文后,首次刷成 ALIVE_TTL=60s,
                     之后每 HEARTBEAT_INTERVAL=20s 续租到 60s
- WAKE_LOCK    : 唤醒函数的幂等锁,SET NX EX 30,防止同时多次扫队列

为什么需要两段 TTL(D-Y):
v3.2 设计里 try_acquire 直接用 60s TTL,但 enqueue → worker 拿到 task → 进入 heartbeat
之间可能 > 60s(尤其 worker 重启 / 高峰排队),ALIVE_KEY 过期 → reconcile 误判僵尸 →
SREM ACTIVE_SET → 后启动的 task 不知道自己已被踢出,并发计数失真。
分两段后:
- 任务入队前 5 分钟内必然有 ALIVE_KEY,reconcile 不误杀
- 任务进入 heartbeat 后用更短的 60s TTL,异常崩溃在 1 分钟内被 reconcile 清理

API 调用方语义:
- /start 时 try_acquire,占成功 → 入队 start_workflow_task,Project.status='extracting'
                       占失败 → Project.status='queued',不入队
- /review /confirm-outline /retry 时 try_acquire,占成功 → 入队;占失败 → 503 + Retry-After
- worker:task 入口立刻起 heartbeat 上下文(自动首次 SET 60s);task 结束 release + wake
- worker 启动:先 reconcile_active_projects 扫一遍清僵尸;再 wake 一次处理漏唤醒的 queued
"""

import asyncio
import contextlib
import redis.asyncio as redis_async
import sqlalchemy as sa
import structlog

from ..config import settings
from ..db import session_factory

log = structlog.get_logger()

ACTIVE_SET = "bid_app:active_projects"
ALIVE_KEY = "bid_app:project_alive:{}"
WAKE_LOCK = "bid_app:wake_in_flight"

# D-Y 双 TTL
RESERVE_TTL = 300       # API try_acquire 设的 TTL,覆盖 enqueue→worker 启动延迟
ALIVE_TTL = 60          # task heartbeat 续租用的较短 TTL,反映"现在真在跑"
HEARTBEAT_INTERVAL = 20  # heartbeat 周期,< ALIVE_TTL 的一半留容错


def _r() -> redis_async.Redis:
    return redis_async.from_url(settings.redis_url, decode_responses=True)


async def try_acquire_project_slot(project_id: int) -> str | None:
    """⭐ D-AB lease token 修正:返回 lease token(uuid)表示占到名额;返回 None 表示满。
    ALIVE_KEY 存的是 token 字符串,task 入口校验 token 还匹配才算"我仍持有 slot"。

    为什么不用纯布尔:
    v3.3 用 RESERVE_TTL=300s 缓解 ALIVE_KEY 过早过期的窗口,但 worker 重启或排队 > 5min
    时 ALIVE_KEY 会过期 → reconcile 把 ACTIVE_SET 里的项目清掉 → 之后 task 起来时**不
    知道**自己已被踢出 → 实际并发可能超 10。lease token 让 task 入口能精确判断"我的
    slot 是否还活着"。
    """
    import uuid
    token = uuid.uuid4().hex

    script = """
        local size = redis.call('SCARD', KEYS[1])
        local max = tonumber(ARGV[1])
        if size < max then
            local added = redis.call('SADD', KEYS[1], ARGV[2])
            redis.call('SET', KEYS[2], ARGV[3], 'EX', tonumber(ARGV[4]))
            return added
        end
        return 0
    """
    r = _r()
    try:
        ok = await r.eval(
            script, 2, ACTIVE_SET, ALIVE_KEY.format(project_id),
            settings.max_concurrent_projects, project_id, token, RESERVE_TTL,
        )
        return token if ok == 1 else None
    finally:
        await r.aclose()


async def ensure_project_slot(project_id: int, token: str) -> bool:
    """task 入口调用:校验自己的 token 仍是 ALIVE_KEY 的值。
    True = slot 仍归我,可继续;False = 已被回收,task 应当退出或重新 acquire。"""
    r = _r()
    try:
        current = await r.get(ALIVE_KEY.format(project_id))
        return current == token
    finally:
        await r.aclose()


async def heartbeat_project(project_id: int, token: str) -> None:
    """task 跑起来后续租到较短的 ALIVE_TTL(60s)。
    用 Lua CAS:仅当 ALIVE_KEY 的值仍是自己的 token 时才续租;否则不动
    (说明 reconcile 已清理,token 失效,应停止续租 + task 退出)。"""
    script = """
        local cur = redis.call('GET', KEYS[1])
        if cur == ARGV[1] then
            redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
            return 1
        end
        return 0
    """
    r = _r()
    try:
        await r.eval(script, 1, ALIVE_KEY.format(project_id), token, ALIVE_TTL)
    finally:
        await r.aclose()


async def release_project_slot(project_id: int, token: str | None = None) -> None:
    """SREM + DEL alive。允许重复调用(SREM 不存在的 member 是 no-op)。
    token 不为 None 时用 Lua CAS,仅当持有 token 才释放,防止误释放别人重 acquire 的 slot。"""
    if token is None:
        # 强制释放(仅 reconcile / 补偿动作用)
        r = _r()
        try:
            async with r.pipeline(transaction=True) as p:
                p.srem(ACTIVE_SET, project_id)
                p.delete(ALIVE_KEY.format(project_id))
                await p.execute()
        finally:
            await r.aclose()
        return

    # 带 token 的安全释放
    script = """
        local cur = redis.call('GET', KEYS[1])
        if cur == ARGV[1] then
            redis.call('DEL', KEYS[1])
            redis.call('SREM', KEYS[2], ARGV[2])
            return 1
        end
        return 0
    """
    r = _r()
    try:
        await r.eval(
            script, 2, ALIVE_KEY.format(project_id), ACTIVE_SET,
            token, project_id,
        )
    finally:
        await r.aclose()


async def reconcile_active_projects() -> list[int]:
    """worker 启动时调:active set 里 alive key 已不存在 → 视为僵尸,从 set 移除。
    返回被清理的 project_ids 列表(调用方可决定是否把它们标 failed)。"""
    r = _r()
    try:
        members = await r.smembers(ACTIVE_SET)
        if not members:
            return []
        # pipeline EXISTS 批量查
        async with r.pipeline(transaction=False) as p:
            for pid in members:
                p.exists(ALIVE_KEY.format(pid))
            results = await p.execute()
        zombies = [int(pid) for pid, alive in zip(members, results) if not alive]
        if zombies:
            await r.srem(ACTIVE_SET, *zombies)
            log.warning("reconciled_zombie_projects", project_ids=zombies)
        return zombies
    finally:
        await r.aclose()


async def wake_queued_projects(arq_pool) -> int:
    """幂等地把 status='queued' 的项目按 FIFO 入队。SETNX 排他锁防并发。"""
    r = _r()
    try:
        got = await r.set(WAKE_LOCK, "1", nx=True, ex=30)
        if not got:
            return 0
        try:
            async with session_factory() as s:
                while True:
                    # 先看名额:如果已经满,直接退出
                    size = await r.scard(ACTIVE_SET)
                    if size >= settings.max_concurrent_projects:
                        return 0
                    # 取一个 queued 项目(FIFO + 行锁,跨进程安全)
                    async with s.begin():
                        row = await s.execute(sa.text(
                            "SELECT id FROM projects WHERE status='queued' "
                            "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                        ))
                        next_pid = row.scalar_one_or_none()
                        if next_pid is None:
                            return 0
                        # 占名额(返回 token);失败任一步都会让事务回滚保持 queued
                        slot_token = await try_acquire_project_slot(next_pid)
                        if slot_token is None:
                            return 0
                        run_row = await s.execute(sa.text(
                            "SELECT id, langgraph_thread_id FROM runs "
                            "WHERE project_id=:p ORDER BY started_at DESC LIMIT 1"
                        ), {"p": next_pid})
                        run = run_row.one_or_none()
                        if run is None:
                            await release_project_slot(next_pid, slot_token)
                            return 0
                        run_id, thread_id = run
                        await s.execute(sa.text(
                            "UPDATE projects SET status='running' WHERE id=:p"
                        ), {"p": next_pid})
                    # commit 后再 enqueue;若 enqueue 抛异常,补偿:释放 slot + 把项目改回 queued
                    try:
                        await arq_pool.enqueue_job(
                            "start_workflow_task",
                            project_id=next_pid, run_id=run_id, thread_id=thread_id,
                            slot_token=slot_token,
                        )
                    except Exception:
                        log.exception("wake_enqueue_failed", project_id=next_pid)
                        await release_project_slot(next_pid, slot_token)
                        async with session_factory() as s2:
                            await s2.execute(sa.text(
                                "UPDATE projects SET status='queued' WHERE id=:p"
                            ), {"p": next_pid})
                            await s2.commit()
                        return 0   # 让外层重试(下次 release 触发)
                    # 循环看下一个,直到名额满或队列空
        finally:
            await r.delete(WAKE_LOCK)
    finally:
        await r.aclose()


@contextlib.asynccontextmanager
async def project_heartbeat(project_id: int, token: str):
    """task 运行时上下文,每 HEARTBEAT_INTERVAL 秒续租 alive TTL(带 token CAS)。
    若 token 失效(reconcile 已清理),heartbeat_project 静默 no-op,task 应当感知后退出。"""
    async def _loop():
        while True:
            try:
                await heartbeat_project(project_id, token)
            except Exception:
                log.exception("heartbeat_failed", project_id=project_id)
            await asyncio.sleep(HEARTBEAT_INTERVAL)
    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
```

**worker/lifecycle.py** 启动时调 reconcile + wake:

```python
async def on_startup(ctx):
    saver = AsyncPostgresSaver.from_conn_string(settings.langgraph_dsn)
    await saver.setup()
    ctx["checkpointer"] = saver
    ctx["arq_pool"] = ctx["redis"]   # arq 已经把 redis 连接放在 ctx['redis']
    zombies = await reconcile_active_projects()
    if zombies:
        # 把僵尸项目标 failed,运维可手动 retry(由 /retry 端点或手动改库)
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET status='failed' "
                        "WHERE id = ANY(:ids) AND status='running'"),
                {"ids": zombies},
            )
            await s.commit()
    await wake_queued_projects(ctx["arq_pool"])
```

**worker/tasks.py 的三类任务**(D-T 修正版,人工等待不占名额):

> ⚠️ **设计原则**:slot 代表"当前正在跑 LLM/工作流的项目"。worker 进程结束(因为 `interrupt` 退出 `astream` 循环)heartbeat 必停,alive TTL 过期会被 reconcile 判僵尸——所以 v3-pass1 的"awaiting_review 持续占名额"在物理上也不可能成立。**正确语义**:每个 task 进入时持有名额,任务返回(包括因 interrupt 自然结束)就释放。下一次 resume/retry 由 API 端点先 `try_acquire`,占到才入队;占不到返回 503。

```python
from arq.worker import func


@func(max_tries=1)   # D-Z:不让 arq 自动重试,LangGraph checkpoint 由用户 /retry 触发恢复
async def start_workflow_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                              slot_token: str):
    """全新启动。被 enqueue 时 /start 已 try_acquire 拿到 slot_token。
    task 入口校验 token 仍匹配(D-AB);否则说明 reconcile 已清,重新 acquire 或失败退出。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}

    # ⭐ D-AB:lease token 校验
    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "start")
    if token is None:
        return  # 失败重排:让 reconcile/wake 处理

    try:
        async with project_heartbeat(project_id, token):
            await _set_project_status(project_id, "running")
            initial = await build_initial_state(project_id, run_id)
            async for _ in graph.astream(initial, config, stream_mode="values"):
                pass
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "start_workflow_task crashed",
                               run_id=run_id, thread_id=thread_id, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _set_project_status(project_id, "failed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
async def resume_review_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                             resume_payload: dict, slot_token: str,
                             reviewer_id: int | None = None,
                             chapter_id: int | None = None):
    """从 interrupt 恢复。worker 入口写 ReviewEvent(D-AC),保证事件与执行同生同灭。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "resume")
    if token is None:
        return

    # ⭐ D-AC:worker 入口写 ReviewEvent(章节审核场景)
    if reviewer_id is not None and chapter_id is not None:
        kind = (resume_payload or {}).get("kind")
        if kind == "chapter_review":
            async with session_factory() as s:
                s.add(ReviewEvent(
                    chapter_id=chapter_id, reviewer_id=reviewer_id,
                    decision=resume_payload.get("decision"),
                    feedback_text=resume_payload.get("feedback") or None,
                ))
                await s.commit()
        # outline_confirm 不写 ReviewEvent(不是 review,是项目级动作)

    try:
        async with project_heartbeat(project_id, token):
            async for _ in graph.astream(Command(resume=resume_payload), config,
                                         stream_mode="values"):
                pass
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "resume_review_task crashed",
                               run_id=run_id, payload=resume_payload, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _set_project_status(project_id, "failed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
async def retry_failed_chapter_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                                    chapter_index: int, reviewer_id: int,
                                    chapter_id: int, slot_token: str):
    """API 端点已 try_acquire 成功才入队;DB 重置 + 续跑;worker 入口写 ReviewEvent。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "retry")
    if token is None:
        return

    # ⭐ D-AC:worker 入口写 ReviewEvent(retry_failed)
    async with session_factory() as s:
        s.add(ReviewEvent(chapter_id=chapter_id, reviewer_id=reviewer_id,
                          decision="retry_failed"))
        # DB 重置 chapter:status=pending,retry_count=0,abandoned 本轮版本
        await s.execute(sa.text(
            "UPDATE chapter_versions SET abandoned=true "
            "WHERE chapter_id=:c AND abandoned=false"
        ), {"c": chapter_id})
        await s.execute(sa.text(
            "UPDATE chapters SET status='pending', retry_count=0, last_error=NULL "
            "WHERE id=:c"
        ), {"c": chapter_id})
        await s.commit()

    try:
        async with project_heartbeat(project_id, token):
            await graph.aupdate_state(config, {"retry_count": 0})
            async for _ in graph.astream(None, config, stream_mode="values"):
                pass
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "retry_failed_chapter_task crashed",
                               run_id=run_id, chapter_index=chapter_index, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _set_project_status(project_id, "failed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


async def _ensure_or_reacquire(project_id: int, slot_token: str,
                               run_id: int, action: str) -> str | None:
    """task 入口的 lease 校验。
    1. 若 token 仍匹配 → 返回原 token,直接跑
    2. 若 token 失效但仍有空名额 → 重新 acquire 拿新 token,继续跑
    3. 若 token 失效且没空名额 → 把项目改回 queued,返回 None,task 退出"""
    if await ensure_project_slot(project_id, slot_token):
        return slot_token

    log.warning("slot_token_lost", project_id=project_id, action=action,
                run_id=run_id, hint="reservation TTL expired or reconciled")

    new_token = await try_acquire_project_slot(project_id)
    if new_token:
        log.info("slot_reacquired", project_id=project_id, action=action)
        return new_token

    # 没空名额:把项目状态切回 queued(start) 或 awaiting_review/failed(resume/retry)
    async with session_factory() as s:
        if action == "start":
            await s.execute(sa.text(
                "UPDATE projects SET status='queued' WHERE id=:p"
            ), {"p": project_id})
        # resume/retry:status 由用户重新点 /review /retry 时再修;此处什么都不做
        await s.commit()
    log.warning("slot_lost_no_capacity", project_id=project_id, action=action)
    return None


async def _project_dir(project_id: int) -> Path:
    """从 DB 取项目目录,用于错误日志写入。所有调用方必须 await。"""
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"), {"p": project_id},
        )
        return Path(row.scalar_one())
```

> 注:模块顶部加 `import traceback`。`append_error` 在写日志失败时已自吞异常,但保险起见外层也包一层 try,确保异常路径不会因日志失败而二次崩溃。

> `arq` `WorkerSettings.max_jobs` 同步设为 `MAX_CONCURRENT_PROJECTS`,即便业务 SET 漂移,worker 物理上也不会超并发。两层独立。

**API 端点的 acquire 责任**(`/start` `/review` `/confirm-outline` `/retry` 都要做):

```python
# 伪代码,真实端点见 §15
acquired = await try_acquire_project_slot(project_id)
slot_token = await try_acquire_project_slot(project_id)
if slot_token is None:
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="并发上限已达,请 1 分钟后重试",
        headers={"Retry-After": "60"},
    )
try:
    await arq_pool.enqueue_job(..., slot_token=slot_token)
except Exception:
    await release_project_slot(project_id, slot_token)   # 补偿
    raise HTTPException(503, "无法入队任务,请稍后重试")
```

`/start` 仍可以选 queued 而非 503(因为是新项目,排队等待是合理 UX);resume/review/retry 用户在等审核交互响应,503 让前端立刻 toast 重试更合适。

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
    timeout_seconds: int | None = None, **kw,
) -> tuple[dict, StreamResult]:
    """非流式(LLM-1 / LLM-3 用)+ 重试 + 超时 + JSON 解析。

    与 call_llm_stream 不同:
    - stream=False,一次性拿 response
    - response_format=json_object 强制 JSON
    - 超时默认 120s(提纲生成 / 可视化建议都不该 10 分钟)

    返回 (parsed_json, stream_result)。stream_result.text 是原始 JSON 字符串。
    """
    if _FAKE:
        return await _fake_json(model, messages, project_id)

    backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
    timeout = timeout_seconds or 120

    async with asyncio.timeout(timeout):
        last_err: Exception | None = None
        for attempt in range(settings.llm_retry_max + 1):
            try:
                response = await litellm.acompletion(
                    model=model, messages=messages, api_key=api_key,
                    stream=False,
                    response_format={"type": "json_object"},
                    **kw,
                )
                content = response.choices[0].message.content or "{}"
                usage = getattr(response, "usage", None)
                p_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
                c_tok = getattr(usage, "completion_tokens", 0) if usage else 0

                await record_token_usage(
                    user_id=user_id, project_id=project_id, run_id=run_id,
                    model=model, prompt_tokens=p_tok, completion_tokens=c_tok,
                )

                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as je:
                    log.warning("llm_json_parse_failed", model=model, content_head=content[:200])
                    raise LLMRetryFailed(f"json parse: {je}") from je

                return parsed, StreamResult(text=content,
                                            prompt_tokens=p_tok,
                                            completion_tokens=c_tok)

            except (RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout) as e:
                last_err = e
                log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                raise LLMRetryFailed(str(e)) from e
            except LLMRetryFailed:
                # JSON 解析失败也走重试链(模型偶尔吐非 JSON)
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                raise

    raise LLMRetryFailed(str(last_err))


async def _fake_json(model: str, messages, project_id: int):
    """测试桩,与 _fake_stream 配套。"""
    fake = {
        "chapters": [
            {"id": "ch_01", "title": "测试章节 1", "summary": "测试摘要",
             "key_points": ["点1", "点2"], "target_pages": 2,
             "matched_scoring_items": ["1.1"]},
            {"id": "ch_02", "title": "测试章节 2", "summary": "测试摘要 2",
             "key_points": ["点A", "点B"], "target_pages": 3,
             "matched_scoring_items": ["2.1"]},
        ]
    }
    return fake, StreamResult(text=json.dumps(fake, ensure_ascii=False),
                              prompt_tokens=50, completion_tokens=80)
```

> 注意 `import json` 在文件顶部加上。`response_format={"type":"json_object"}` 由 LiteLLM 透传给 DashScope;若 DashScope 某模型不支持,降级方案是在 prompt 末尾追加"严格输出 JSON,不要任何多余文本"并放宽容错(LLM-1 / LLM-3 已经在 §10.3 中如此设计)。

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
    """⭐ D-C 真快照:直接从 Project.encrypted_api_key_snapshot 读,
    与用户当前的 ApiKey 表完全解耦。这样:
    - 用户重置 / 删除 ApiKey 后,已启动项目继续用快照(FR-7.6)
    - 即便用户账号被禁用,工作流也能跑完(因为快照在 Project 上)
    """
    async with session_factory() as s:
        row = await s.execute(
            select(Project.encrypted_api_key_snapshot).where(Project.id == project_id)
        )
        encrypted = row.scalar_one_or_none()
    if encrypted is None:
        raise RuntimeError(f"project {project_id} has no api_key snapshot; "
                           "did /start succeed?")
    return decrypt_api_key(encrypted)


async def _resolve_user_id(project_id: int) -> int:
    """token_usage 记账要 user_id,用 api_key_owner(快照时锁定的启动者)。"""
    async with session_factory() as s:
        row = await s.execute(
            select(Project.api_key_owner).where(Project.id == project_id)
        )
        return row.scalar_one()
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

修复点:
- **D-L** 缓存路径**固定** `proposal.docx`,文件名展示靠下载端 FileResponse(filename)
- **D-N** mermaid 用 `re.finditer` + 反向 span 替换,容忍 CRLF / 行尾空格
- **D-H** Redis 锁用 `redis.asyncio.Lock` 自带 token CAS,不再手撸 `del`
- mermaid 图片在 markdown 用**相对路径**;pandoc 用 `--resource-path` 解析

```python
"""DOCX 导出 — D5 简化方案:mermaid 预渲染 + pandoc 直转。
全局串行(D-H):asyncio.Lock + Redis Lock 双层。"""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis_async
import structlog

from ..config import settings

log = structlog.get_logger()
_module_lock = asyncio.Lock()  # 同进程内锁
_REDIS_LOCK_KEY = "bid_app:lock:docx_export"
_REDIS_LOCK_TTL = 300              # 秒,大于一次 docx 生成耗时
_REDIS_LOCK_BLOCKING_TIMEOUT = 120 # 等位最长 2 分钟超时(D-AA);v3.3 是 600s 但
# 等锁的 DOCX 任务会占着 arq worker job 槽,与 workflow task 抢 max_jobs 名额;
# 缩短到 2 分钟 + max_jobs 留余量,等不到锁就让任务失败,前端可重试

# 兼容 ``` 与 ~~~ 围栏,容忍 ``` 后面的语言名前后空格、行尾 \r、
# 闭合 fence 自身在新行开头(可有空格)。
MERMAID_RE = re.compile(
    r"(?P<fence>```|~~~)[ \t]*mermaid[ \t]*\r?\n"
    r"(?P<code>.*?)\r?\n"
    r"(?P=fence)[ \t]*(?=\r?\n|$)",
    re.DOTALL,
)


async def export_docx(
    *, markdown: str, project_dir: Path, project_name: str,
    reference_doc: Path, redis_url: str,
) -> Path:
    """串行化包装。返回最终 docx 路径(固定为 {project_dir}/proposal.docx)。"""
    async with _module_lock:  # 进程内,即时排队
        async with _redis_lock(redis_url):  # 跨进程(未来扩容)
            return await _export_docx_inner(markdown, project_dir, reference_doc)


@asynccontextmanager
async def _redis_lock(redis_url: str):
    """正确的 Redis 互斥锁:用 redis.asyncio.Lock(token + Lua CAS,自带阻塞等待)。"""
    r = redis_async.from_url(redis_url)
    lock = r.lock(
        _REDIS_LOCK_KEY,
        timeout=_REDIS_LOCK_TTL,            # 持锁 TTL,自动过期
        blocking=True,
        blocking_timeout=_REDIS_LOCK_BLOCKING_TIMEOUT,
        thread_local=False,
    )
    acquired = await lock.acquire()
    if not acquired:
        await r.aclose()
        raise TimeoutError(f"docx export lock timeout after {_REDIS_LOCK_BLOCKING_TIMEOUT}s")
    try:
        yield
    finally:
        try:
            await lock.release()
        except Exception:
            log.exception("redis_lock_release_failed")
        await r.aclose()


async def _export_docx_inner(markdown: str, project_dir: Path,
                             reference_doc: Path) -> Path:
    work = project_dir / "docx-build"
    work.mkdir(parents=True, exist_ok=True)

    # 1. mermaid 预渲染(图片用相对路径,后面 pandoc 用 --resource-path 解析)
    inlined = await _render_mermaid(markdown, work)

    md_path = work / "proposal_inlined.md"
    md_path.write_text(inlined, encoding="utf-8")

    out_path = project_dir / "proposal.docx"   # ⭐ D-L 固定缓存名

    # 2. pandoc 直转
    args = [
        "pandoc",
        str(md_path),
        "-o", str(out_path),
        "--resource-path", str(work),  # 让相对图片路径(./mmd_0.png)能解析
        "--standalone",
    ]
    if reference_doc.exists():
        args.append(f"--reference-doc={reference_doc}")
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
    """逐个 mermaid 块渲染 PNG,markdown 中替换为图片引用。

    用 re.finditer 找出所有 span,**反向**替换(从后往前),
    防止前一个替换改变后一个 span 的偏移。
    """
    matches = list(MERMAID_RE.finditer(markdown))
    if not matches:
        return markdown

    # 渲染所有块,失败的留 None,后续保留原 fence
    rendered: list[Path | None] = []
    for i, m in enumerate(matches):
        code = m.group("code")
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
            rendered.append(png)
        else:
            log.warning("mermaid_render_failed",
                        index=i, error=err.decode(errors="replace"))
            rendered.append(None)

    # 反向替换(从最后一个 match 起,改原文不影响前面 match.start/end)
    out = markdown
    for m, png in zip(reversed(matches), reversed(rendered)):
        if png is None:
            continue  # 保留原 fence 块,降级容错
        # 图片用文件名(相对路径,pandoc --resource-path 找得到)
        replacement = f"![]({png.name})"
        out = out[:m.start()] + replacement + out[m.end():]

    return out


def _sanitize(name: str) -> str:
    """文件名安全化(给下载端展示用,与 _export_docx_inner 解耦)。"""
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name)[:80] or "proposal"
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
from pathlib import Path
import sqlalchemy as sa

from ..config import settings
from ..db import session_factory
from ..services.docx_export import export_docx


async def generate_docx_task(ctx, *, project_id: int, docx_job_id: int) -> dict:
    """串行锁在 export_docx 内部实现(D-H)。"""
    # 1. 取项目 markdown + dir + name
    async with session_factory() as s:
        prj_row = await s.execute(
            sa.text("SELECT name, dir_path FROM projects WHERE id=:p"),
            {"p": project_id},
        )
        project_name, project_dir_str = prj_row.one()
        project_dir = Path(project_dir_str)

        # 取最新 Run 的 final_proposal(由 assemble 节点写入,具体落在哪里看 §10)
        run_row = await s.execute(
            sa.text("SELECT id FROM runs WHERE project_id=:p AND status='done' "
                    "ORDER BY finished_at DESC LIMIT 1"),
            {"p": project_id},
        )
        run_id = run_row.scalar_one_or_none()
        if run_id is None:
            raise RuntimeError(f"project {project_id} has no completed run")

        # final_proposal 我们存到磁盘 {project_dir}/proposal.md(由 assemble 节点写)
        md_path = project_dir / "proposal.md"
        if not md_path.exists():
            raise RuntimeError(f"proposal.md missing at {md_path}")
        markdown = md_path.read_text(encoding="utf-8")

    # 2. 标记进入 rendering 阶段
    async with session_factory() as s:
        await s.execute(
            sa.text("UPDATE docx_jobs SET status='rendering_mermaid' WHERE id=:i"),
            {"i": docx_job_id},
        )
        await s.commit()

    # 3. 真正执行(锁在 export_docx 内)
    try:
        out_path = await export_docx(
            markdown=markdown,
            project_dir=project_dir,
            project_name=project_name,
            reference_doc=Path(settings.templates_dir) / "reference.docx",
            redis_url=settings.redis_url,
        )
    except Exception as e:
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE docx_jobs SET status='failed', error=:e, "
                        "finished_at=NOW() WHERE id=:i"),
                {"i": docx_job_id, "e": str(e)[:4000]},
            )
            await s.commit()
        raise

    # 4. 落 done(output_path 固定为 proposal.docx)
    async with session_factory() as s:
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

### 14.3 全局限流 + 登录失败锁(D-Q)

需求 NFR-4 要求"全局每 IP 100 req/min"+ FR-6.7 要求"登录失败 5 次/分钟,超过锁 5 分钟"。slowapi 只能做请求级速率限制,无法做"失败次数"——所以拆两层:

#### 14.3.1 全局限流 `core/rate_limit.py`

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    default_limits=[settings.global_rate_limit],   # "100/minute"
)
```

`main.py` 注册:

```python
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

> ⚠️ 仅注册 `app.state.limiter` 是不够的——SlowAPI 还需要每个端点用 `@limiter.limit(...)` 装饰才会真生效。FastAPI 全局 default_limits 通过 `Limiter(application_limits=...)` 或 `SlowAPIMiddleware` 才会自动应用。我们用中间件方式:
>
> ```python
> from slowapi.middleware import SlowAPIMiddleware
> app.add_middleware(SlowAPIMiddleware)
> ```

#### 14.3.2 登录失败锁 `core/login_throttle.py`

```python
"""FR-6.7:同一 IP 每分钟登录失败 ≥ 5 次 → 锁该 IP 5 分钟。
登录成功清零计数。"""

import redis.asyncio as redis_async
from ..config import settings

_FAIL_KEY = "bid_app:login_fail:{ip}"
_LOCK_KEY = "bid_app:login_lock:{ip}"


def _r() -> redis_async.Redis:
    return redis_async.from_url(settings.redis_url, decode_responses=True)


async def is_locked(ip: str) -> bool:
    r = _r()
    try:
        return (await r.get(_LOCK_KEY.format(ip=ip))) is not None
    finally:
        await r.aclose()


async def record_fail(ip: str) -> bool:
    """记录一次失败。返回 True 表示这次失败之后该 IP 已被锁。"""
    r = _r()
    try:
        n = await r.incr(_FAIL_KEY.format(ip=ip))
        if n == 1:
            await r.expire(_FAIL_KEY.format(ip=ip), 60)  # 1 分钟窗口
        if n >= settings.login_fail_max_per_minute:
            await r.set(_LOCK_KEY.format(ip=ip), "1",
                        ex=settings.login_lock_seconds)
            return True
        return False
    finally:
        await r.aclose()


async def clear_fails(ip: str) -> None:
    """登录成功后清零(锁还在的话不动,等过期)。"""
    r = _r()
    try:
        await r.delete(_FAIL_KEY.format(ip=ip))
    finally:
        await r.aclose()
```

### 14.4 安全头中间件 `core/security_headers.py`

```python
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # CSP 内网 SPA 友好版(允许 inline style 给 react-markdown / mermaid)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'",
        )
        return response
```

`main.py` 在 SlowAPIMiddleware 之后注册:

```python
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TraceIdMiddleware)  # 见 §19.2
```

### 14.5 依赖注入(`deps.py`)

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

### 14.6 完整登录流程示例(`api/auth.py`)

```python
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import (
    create_access_token, create_refresh_token, verify_password,
)
from ..core.login_throttle import is_locked, record_fail, clear_fails
from ..deps import get_db, get_current_user_lax
from ..models import User
from ..schemas.auth import LoginRequest, MeResponse

router = APIRouter()


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """FR-6.7 失败 5 次/min 锁 5 分钟,使用 Redis 计数(D-Q),不依赖 slowapi 的速率限流。"""
    ip = get_remote_address(request)

    # 0. 锁定中直接 429
    if await is_locked(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多,请 5 分钟后再试",
        )

    user = (await db.execute(
        select(User).where(User.username == body.username)
    )).scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        locked_now = await record_fail(ip)
        if locked_now:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录失败次数过多,该 IP 已被锁定 5 分钟",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已禁用")

    # 成功 → 清失败计数(锁的 key 不动,过期自然消失)
    await clear_fails(ip)

    user.last_login_at = sa.func.now()
    await db.commit()

    response.set_cookie("access_token", create_access_token(user.id),
                        httponly=True, samesite="strict", max_age=2 * 3600, path="/")
    response.set_cookie("refresh_token", create_refresh_token(user.id),
                        httponly=True, samesite="strict", max_age=7 * 86400,
                        path="/api/auth/refresh")
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
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """启动工作流。**真快照**当前用户的 ApiKey 加密载荷到 Project(D-C)。"""
    project = await _get_project_owned_or_412(db, project_id, user)

    # 拿当前用户的 ApiKey 加密载荷
    api_key = (await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == "dashscope")
    )).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(412, "请先配置 DashScope API Key")

    # 真快照:把当前 encrypted_key 拷贝到 project.encrypted_api_key_snapshot
    project.api_key_owner = user.id
    project.encrypted_api_key_snapshot = api_key.encrypted_key  # ⭐ FR-7.6
    project.pages_per_chapter = body.pages_per_chapter
    project.max_retry_per_chapter = body.max_retry_per_chapter

    # 创建 Run + thread_id(状态先 init,看名额能不能占)
    thread_id = f"run-{project_id}-{secrets.token_hex(8)}"
    run = Run(project_id=project_id, langgraph_thread_id=thread_id,
              started_at=datetime.now(timezone.utc), status="running")
    db.add(run)
    await db.flush()  # 拿 run.id

    # ⭐ D-T:在 commit 之前决定占名额还是 queued(返回 lease token,D-AB)
    slot_token = await try_acquire_project_slot(project_id)
    if slot_token is not None:
        project.status = "extracting"
    else:
        project.status = "queued"
    await db.commit()

    # 占名额成功才入队;失败 → 补偿(D-U):回退 status + 标 Run aborted + release slot,返回 503
    if slot_token is not None:
        arq_pool = request.app.state.arq_pool
        try:
            await arq_pool.enqueue_job(
                "start_workflow_task",
                project_id=project_id, run_id=run.id, thread_id=thread_id,
                slot_token=slot_token,
            )
        except Exception as e:
            log.exception("start_enqueue_failed", project_id=project_id, run_id=run.id)
            project.status = "init"
            run.status = "aborted"
            run.finished_at = datetime.now(timezone.utc)
            run.error = f"enqueue failed: {e!r}"
            await db.commit()
            await release_project_slot(project_id, slot_token)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="无法入队工作流任务,请稍后重试 /start",
            ) from e
    return {"run_id": run.id, "queued": slot_token is None}


@router.put("/{project_id}/outline")
async def confirm_outline(
    project_id: int,
    body: OutlineConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """P4 提纲确认(D-K)。
    body.chapters 为空 → 自动确认沿用 LLM-1 提纲;
    非空 → 用编辑后的版本(校验:每章必须有 title / key_points / target_pages)。
    """
    project = await db.get(Project, project_id)
    if project is None or project.status != "outline_ready":
        raise HTTPException(409, f"project status must be outline_ready, got {project and project.status}")

    if body.chapters:
        for c in body.chapters:
            if not c.get("title") or not c.get("key_points") or not c.get("target_pages"):
                raise HTTPException(400, "every chapter needs title/key_points/target_pages")

    run = await _get_active_run(db, project_id)

    slot_token = await try_acquire_project_slot(project_id)
    if slot_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="系统繁忙,请稍后重试",
            headers={"Retry-After": "60"},
        )
    arq_pool = request.app.state.arq_pool
    try:
        await arq_pool.enqueue_job(
            "resume_review_task",
            project_id=project_id, run_id=run.id,
            thread_id=run.langgraph_thread_id,
            resume_payload={"kind": "outline_confirm",
                            "chapters": body.chapters or []},
            slot_token=slot_token,
            # outline_confirm 不写 ReviewEvent,所以不传 reviewer_id/chapter_id
        )
    except Exception:
        await release_project_slot(project_id, slot_token)
        raise HTTPException(503, "无法入队提纲确认任务,请稍后重试")
    return {"ok": True}


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-1.6:删除项目(只有创建者 / admin)同时删磁盘目录。
    数据库一侧靠 ON DELETE CASCADE(token_usage / documents / runs / chapters / docx_jobs)。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    if project.created_by != user.id and user.role != "admin":
        raise HTTPException(403, "only creator or admin can delete")

    dir_path = Path(project.dir_path)

    await db.delete(project)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # 数据库提交成功 → 删磁盘目录(失败只 log,不回滚)
    if dir_path.exists():
        try:
            shutil.rmtree(dir_path)
        except Exception:
            log.exception("project_dir_rm_failed", path=str(dir_path))

    return {"ok": True}
```

#### 上传配额校验(NFR-4 单用户日 500MB)

```python
@router.post("/{project_id}/documents")
async def upload_document(
    project_id: int,
    kind: str,
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # ... 文件类型 / 大小校验

    today_used = (await db.execute(
        sa.text(
            "SELECT COALESCE(SUM(d.file_size), 0) FROM documents d "
            "JOIN projects p ON p.id = d.project_id "
            "WHERE p.created_by = :u "
            "AND d.created_at >= date_trunc('day', NOW() AT TIME ZONE :tz)"
        ),
        {"u": user.id, "tz": settings.tz},
    )).scalar_one()

    daily_quota_bytes = settings.daily_upload_quota_mb * 1024 * 1024
    if today_used + file.size > daily_quota_bytes:
        raise HTTPException(
            413, f"今日上传配额已用 {today_used // 1024 // 1024}MB,"
                 f"上限 {settings.daily_upload_quota_mb}MB"
        )

    # ... 保存 + INSERT documents
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

    # ⭐ D-AD 状态校验 + 行锁:防止重复审或对非 awaiting_review 章节审
    chapter = (await db.execute(
        sa.text("SELECT * FROM chapters WHERE run_id=:r AND index=:i FOR UPDATE"),
        {"r": run.id, "i": idx},
    )).mappings().one_or_none()
    if chapter is None:
        raise HTTPException(404, "chapter not found")
    if chapter["status"] != "awaiting_review":
        raise HTTPException(409,
            f"chapter is {chapter['status']}, only awaiting_review can be reviewed")

    # ⭐ D-T:resume 前 try_acquire(返回 lease token,D-AB)
    slot_token = await try_acquire_project_slot(project_id)
    if slot_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="系统繁忙(并发上限已达),请稍后重试",
            headers={"Retry-After": "60"},
        )

    arq_pool = request.app.state.arq_pool
    try:
        await arq_pool.enqueue_job(
            "resume_review_task",
            project_id=project_id, run_id=run.id,
            thread_id=run.langgraph_thread_id,
            resume_payload={
                "kind": "chapter_review",
                "decision": body.decision,
                "feedback": body.feedback or "",
            },
            slot_token=slot_token,
            reviewer_id=user.id,
            chapter_id=chapter["id"],
        )
    except Exception:
        await release_project_slot(project_id, slot_token)   # 补偿
        raise HTTPException(503, "无法入队审核任务,请稍后重试")

    # ⭐ D-AC:ReviewEvent 由 worker 入口写,API 不再写
    await db.commit()  # 释放行锁
    return {"ok": True}


@router.post("/{project_id}/chapters/{idx}/retry")
async def retry_chapter(
    project_id: int,
    idx: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-4.7:仅 status='failed' 章节可触发;状态机重置 + ReviewEvent 由 worker
    入口在事务内做(D-AC),API 这里只校验 + acquire + enqueue。"""
    run = await _get_active_run(db, project_id)

    # 行锁 + 状态校验
    chapter = (await db.execute(
        sa.text("SELECT * FROM chapters WHERE run_id=:r AND index=:i FOR UPDATE"),
        {"r": run.id, "i": idx},
    )).mappings().one_or_none()
    if chapter is None:
        raise HTTPException(404, "chapter not found")
    if chapter["status"] != "failed":
        raise HTTPException(409, f"chapter is {chapter['status']}, not failed")

    slot_token = await try_acquire_project_slot(project_id)
    if slot_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="系统繁忙,请稍后重试",
            headers={"Retry-After": "60"},
        )
    arq_pool = request.app.state.arq_pool
    try:
        await arq_pool.enqueue_job(
            "retry_failed_chapter_task",
            project_id=project_id, run_id=run.id,
            thread_id=run.langgraph_thread_id,
            chapter_index=idx,
            chapter_id=chapter["id"],
            reviewer_id=user.id,
            slot_token=slot_token,
        )
    except Exception:
        await release_project_slot(project_id, slot_token)
        raise HTTPException(503, "无法入队重试任务,请稍后重试")

    # ReviewEvent 由 worker 入口写,这里只释放行锁
    await db.commit()
    return {"ok": True}
```

### 15.3 DOCX 生成与下载(`api/docx.py`)

修复点(D-L / D-M):
- 缓存路径**固定** `{project_dir}/proposal.docx`,展示文件名走 `Content-Disposition`
- 入队前先 INSERT DocxJob 拿到 id,再 enqueue,然后 UPDATE arq_job_id

```python
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import get_current_user, get_db
from ..models import DocxJob, Project
from ..services.docx_export import _sanitize


router = APIRouter()


def _display_filename(project_name: str) -> str:
    """FR-5.6:`{project_name}_技术方案_{YYYYMMDD}.docx`,YYYYMMDD 用 Asia/Shanghai。"""
    today = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d")
    return f"{_sanitize(project_name)}_技术方案_{today}.docx"


@router.post("/{project_id}/proposal.docx")
async def trigger_docx(
    project_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_done_project(db, project_id)

    # 已缓存 → 不重生成,前端轮询 /docx-job/null 自然知道(此处直接告诉前端)
    cached = Path(project.dir_path) / "proposal.docx"
    if cached.exists():
        return {"job_id": None, "cached": True}

    # ⭐ D-M / D-S:先 INSERT(arq_job_id NULL,占位拿主键),
    # partial unique on (project_id) WHERE status IN ('pending','rendering_mermaid','pandoc')
    # 会在并发第二个 POST 时直接抛 IntegrityError → 转 409,不需要应用层抢锁。
    from sqlalchemy.exc import IntegrityError
    docx_job = DocxJob(project_id=project_id, arq_job_id=None, status="pending")
    db.add(docx_job)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "该项目已有 DOCX 生成任务在进行中")
    job_pk = docx_job.id

    arq_pool = request.app.state.arq_pool
    job = await arq_pool.enqueue_job(
        "generate_docx_task",
        project_id=project_id, docx_job_id=job_pk,
    )
    if job is None:
        await db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "无法入队 DOCX 任务,请稍后重试")

    docx_job.arq_job_id = job.job_id
    await db.commit()

    return {"job_id": job.job_id, "docx_job_id": job_pk, "cached": False}


@router.get("/{project_id}/proposal.docx")
async def download_docx(
    project_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_done_project(db, project_id)
    path = Path(project.dir_path) / "proposal.docx"   # ⭐ D-L 固定缓存名
    if not path.exists():
        raise HTTPException(409, "请先 POST 触发生成")

    fname = _display_filename(project.name)
    # 同时给 ASCII 兜底名,防止旧浏览器解析中文文件名失败
    ascii_fallback = "proposal.docx"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; "
                f"filename*=UTF-8''{quote(fname)}"
            ),
        },
    )


async def _get_done_project(db: AsyncSession, project_id: int) -> Project:
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    if p.status != "done":
        raise HTTPException(409, f"project not done yet, status={p.status}")
    return p
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

### 15.5 API Key 测试连通(`api/me.py`,D-G 配套)

新增 `GET /api/me/api-key/test`:用当前用户已保存的 Key 发一个最小请求验连通。这是从 `/health` 拆出来的端点(D-G),给配置页面"测试连通"按钮用。

```python
@router.get("/api-key/test")
async def test_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    api_key = (await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == "dashscope")
    )).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(412, "尚未配置 API Key")
    plaintext = decrypt_api_key(api_key.encrypted_key)
    try:
        await api_key_validator.validate_dashscope(plaintext)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    api_key.last_validated_at = sa.func.now()
    await db.commit()
    return {"ok": True}
```

#### 既有的 PUT /api-key

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
# ⭐ 加 postgresql-client-16 给 cron pg_dump 用(D-O / 修 v2 备份跑不起来)
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        chromium \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        nodejs \
        npm \
        curl \
        gnupg \
        lsb-release \
        tzdata \
        supervisor \
        cron \
        ca-certificates \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
       gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-16 \
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

# entrypoint(D-O:先 alembic upgrade,再 exec supervisord)
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# cron 备份
COPY docker/pg-backup.sh /usr/local/bin/pg-backup.sh
RUN chmod +x /usr/local/bin/pg-backup.sh \
    && echo "0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1" \
       > /etc/cron.d/bid-app-backup \
    && chmod 0644 /etc/cron.d/bid-app-backup

# 持久化目录(volume 会盖掉)
RUN mkdir -p /var/lib/bid-app/projects /var/lib/bid-app/backups

EXPOSE 12123

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf", "-n"]
```

`docker/entrypoint.sh`(D-O 关键修复):

```bash
#!/usr/bin/env bash
set -euo pipefail

# 把当前环境变量导出到 /etc/bid-app.env,给 cron 用(cron 任务默认无环境)
{
  echo "# auto-generated by entrypoint, do not edit"
  for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB POSTGRES_HOST POSTGRES_PORT \
           BACKUPS_DIR TZ; do
    eval "echo export $v=\\\"\$$v\\\""
  done
} > /etc/bid-app.env
chmod 600 /etc/bid-app.env

# 等 Postgres 真的可连(防止 healthcheck 还没刷)
echo "[entrypoint] waiting for postgres..."
for i in $(seq 1 60); do
  if PGPASSWORD="$POSTGRES_PASSWORD" pg_isready -h "$POSTGRES_HOST" \
       -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; then
    break
  fi
  sleep 2
done

# DB Migration 同步执行 ⭐ D-O
echo "[entrypoint] alembic upgrade head"
cd /app/backend
/app/backend/.venv/bin/python -m alembic -c /app/backend/alembic.ini upgrade head

# cron 由 supervisord [program:cron] 接管(`/usr/sbin/cron -f` 前台运行),
# 这里**不**再 service cron start,避免双进程争抢 pidfile。

echo "[entrypoint] starting supervisord"
exec "$@"
```

### 17.2 supervisord.conf

⭐ D-O 修复:不再用 supervisord 跑 alembic upgrade(`priority` 不能保证启动顺序);entrypoint 先同步执行 migrate,通过后 `exec supervisord` 起 uvicorn / arq / cron。

```ini
[supervisord]
nodaemon=true
logfile=/var/log/supervisord.log
pidfile=/var/run/supervisord.pid

[program:uvicorn]
command=/app/backend/.venv/bin/uvicorn bid_app.main:app --host 0.0.0.0 --port 12123 --no-access-log
directory=/app/backend
autostart=true
autorestart=true
priority=20
stopsignal=TERM
stopwaitsecs=10
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
stopsignal=TERM
stopwaitsecs=30
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

`worker/settings.py` 关键参数(D-P 兜底并发):

```python
from arq.connections import RedisSettings
from arq.cron import cron
from ..config import settings


class WorkerSettings:
    """⚠️ D-Z:不同任务用不同 max_tries:
    - workflow 三类任务 max_tries=1:LLM 失败 → 章节 status=failed → 用户手动 /retry
      (FR-3.9 / FR-4.7),不让 arq 自动重跑(会绕过 API 端 try_acquire,违反 D-T)。
      崩溃恢复由 LangGraph checkpoint 保证,用户下次 /retry 触发即可从最后成功节点续跑。
    - generate_docx_task max_tries=2:DOCX 失败多是临时(pandoc 偶发),自动重跑 1 次有意义。

    arq 默认全局 max_tries=5,我们在 functions 上分别用 @arq.func 装饰传 max_tries。
    """
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # ⚠️ 不在这里硬编码 functions=list,而是在 worker/tasks.py 用
    # @func(max_tries=1) 装饰器分别配置(arq>=0.26 支持)
    # 这里只列字符串路径供 arq 发现;实际 max_tries 由各 task 装饰器决定
    functions = [
        "bid_app.worker.tasks.start_workflow_task",
        "bid_app.worker.tasks.resume_review_task",
        "bid_app.worker.tasks.retry_failed_chapter_task",
        "bid_app.worker.tasks.generate_docx_task",
    ]

    # ⭐ 给 DOCX 任务留并发余量(D-AA):workflow 上限是 max_concurrent_projects=10,
    # 但 DOCX 也共享这个 worker;若不留余量,10 个 DOCX 在等串行锁会占满 worker
    # job slot,新 workflow task 入队后排在 arq 队列里饿死。+2 余量(1 active DOCX + 1 备用)
    max_jobs = settings.max_concurrent_projects + 2
    job_timeout = 60 * 60 * 4                     # 单 job 上限 4 小时(全章节累计)
    keep_result = 86400
    on_startup = "bid_app.worker.lifecycle.on_startup"
    on_shutdown = "bid_app.worker.lifecycle.on_shutdown"
```

`worker/tasks.py` 用 `@arq.worker.func` 给每个 task 单独设 max_tries(arq 0.26+ 支持):

```python
from arq.worker import func

@func(max_tries=1)
async def start_workflow_task(ctx, *, project_id, run_id, thread_id):
    ...

@func(max_tries=1)
async def resume_review_task(ctx, ...):
    ...

@func(max_tries=1)
async def retry_failed_chapter_task(ctx, ...):
    ...

@func(max_tries=2)
async def generate_docx_task(ctx, *, project_id, docx_job_id):
    ...
```

> 如 arq 装饰器 API 不可用,退而求其次:在 task 内部用 `ctx['job_try']` 检查重试次数,>1 时 `return`(等于自我熔断 arq 重试)。但 `@func(max_tries=...)` 是更干净的方案。

`worker/lifecycle.py` 在 startup 时构建并把 `checkpointer` / `arq_pool` 放到 ctx:

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from ..config import settings

async def on_startup(ctx):
    saver = AsyncPostgresSaver.from_conn_string(settings.langgraph_dsn)
    await saver.setup()
    ctx["checkpointer"] = saver

async def on_shutdown(ctx):
    saver = ctx.get("checkpointer")
    if saver:
        await saver.close()
```

### 17.3 docker-compose.yml

⭐ 修复 v2 的两个问题:
- **改 named volume 为宿主机 bind mount**,与 NFR-2 要求的 `/var/lib/bid-app/...` 一致;运维直接 ls 能看到文件,备份也能直接 cp。
- **使用单一 `.env`**(D-R):由 `gen-secrets.sh` 从 `.env.example` seed + sed 替换占位符生成最终版本,compose 直接读。不用 env_file 列表,因为 compose 的 `${VAR}` 插值只读项目根 `.env`,不读 service 的 env_file。

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
    env_file: .env       # 单一文件;由 scripts/gen-secrets.sh 生成最终版本,
                         # 无 __GENERATE_ME__ 占位符。Postgres 容器的
                         # ${POSTGRES_PASSWORD} 也从这里插值(compose 项目 .env)。
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    volumes:
      - /var/lib/bid-app/projects:/var/lib/bid-app/projects
      - /var/lib/bid-app/backups:/var/lib/bid-app/backups
      - /etc/localtime:/etc/localtime:ro
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
      - /var/lib/bid-app/postgres-data:/var/lib/postgresql/data
      - /etc/localtime:/etc/localtime:ro
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
    # ⭐ D-V:noeviction 而不是 allkeys-lru。Redis 同时承载 arq 队列 / 并发名额 SET /
    # 登录锁 / SSE pub/sub / limiter 计数;LRU 会无声驱逐这些关键 key,导致 silent 故障。
    # noeviction 在内存满时让写入失败,触发监控告警,问题暴露明确。
    # appendonly=yes 让重启后队列与 SET 不丢。
    command: redis-server --maxmemory 200mb --maxmemory-policy noeviction --appendonly yes
    volumes:
      - /var/lib/bid-app/redis-data:/data
      - /etc/localtime:/etc/localtime:ro
    networks: [bid-net]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 6

networks:
  bid-net:
```

部署前确认宿主机目录就绪(`scripts/install.sh` 会自动做):

```bash
sudo mkdir -p /var/lib/bid-app/{postgres-data,redis-data,projects,backups}
# postgres-data 必须属于 uid=999(postgres 镜像 user),不是宿主 root
sudo chown -R 999:999 /var/lib/bid-app/postgres-data
sudo chown -R 1000:1000 /var/lib/bid-app/{projects,backups}
```

### 17.4 备份脚本 `docker/pg-backup.sh`

由 cron 调用。从 entrypoint.sh 写入的 `/etc/bid-app.env` 拿环境变量(cron 默认无环境)。Postgres 客户端在 §17.1 已装到 app 镜像。

```bash
#!/usr/bin/env bash
set -euo pipefail

# 由 cron 调用,环境变量已由 entrypoint.sh 写到 /etc/bid-app.env
# crontab 行 `. /etc/bid-app.env && /usr/local/bin/pg-backup.sh`

: "${POSTGRES_HOST:?missing}"
: "${POSTGRES_USER:?missing}"
: "${POSTGRES_PASSWORD:?missing}"
: "${POSTGRES_DB:?missing}"
: "${BACKUPS_DIR:?missing}"

TS=$(TZ=Asia/Shanghai date +%Y%m%d_%H%M)
OUT="${BACKUPS_DIR}/bid_${TS}.dump"
TMP="${OUT}.partial"

mkdir -p "${BACKUPS_DIR}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -f "${TMP}"

mv "${TMP}" "${OUT}"

# 滚动:保留最近 7 天
find "${BACKUPS_DIR}" -maxdepth 1 -name "bid_*.dump" -mtime +7 -delete

echo "[$(TZ=Asia/Shanghai date +%F\ %T)] backup ok → ${OUT}"
```

> 备份验证(M5 验收):`docker compose exec app pg_restore --list /var/lib/bid-app/backups/bid_xxx.dump | head` 应能列出表名;`./scripts/restore-backup.sh` 在测试服务器跑通一次。

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

### 19.2 项目级 errors.log(D-X / NFR-5)

需求要 LLM 重试 / 章节失败 / DOCX 失败 / 工作流顶层异常都写到 `{project_dir}/errors.log`。这是**与** stdout structlog 并存的:运维平时看 docker logs 整体扫,排查具体项目问题时直接 `tail -f /var/lib/bid-app/projects/{id}/errors.log`。

`core/error_log.py`(JSONL 格式,D-AE):

```python
"""项目级错误日志,JSONL 一行一个 JSON 对象。
为什么 JSONL 而不是 key=val 平文本(D-AE):
- traceback 是多行长字段(可能 > 4KB),平文本格式打破"每行 < PIPE_BUF 原子"假设
- JSONL 把所有字段(含多行 traceback)序列化成单行 JSON 字符串,
  写入单次 syscall 内完成,跨进程并发 append 不交错
- 排查时:`jq` 直接处理,比正则切平文本简单
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from ..config import settings

log = structlog.get_logger()
_lock = asyncio.Lock()


async def append_error(project_dir: Path, message: str, **fields) -> None:
    """JSONL 格式追加一行到 {project_dir}/errors.log。
    永不抛异常(写日志失败不应影响业务流程),失败由 structlog 记录到 stdout。

    示例:
      {"ts":"2026-05-02T12:34:56+08:00", "msg":"LLM exhausted",
       "model":"qwen3.6-max-preview", "traceback":"Traceback (...)\\n  File ..."}
    """
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        log_file = project_dir / "errors.log"
        record = {
            "ts": datetime.now(ZoneInfo(settings.tz)).isoformat(timespec="seconds"),
            "msg": message,
            **{k: v for k, v in fields.items() if v is not None},
        }
        # ensure_ascii=True 确保 \n 等都被转义,line 内不会有换行;separators 紧凑
        line = json.dumps(record, ensure_ascii=False, default=str,
                          separators=(",", ":")) + "\n"
        # 单次 write 调用;append 模式 + Lua 锁(同进程)+ JSON 单行
        # 跨进程并发由 OS append 原子语义保证(单次 < ~4KB write 不会交错)
        async with _lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        log.exception("append_error_failed", project_dir=str(project_dir))
```

> 实际 traceback 通常 1-3KB,JSON 转义后单次 write 仍 < PIPE_BUF。极端长 traceback 的话改成显式 `fcntl.flock` 文件锁,但本项目 10 用户量级用不上。

调用点:

| 调用方 | 何时调 | message 示例 |
|---|---|---|
| `services/llm.py` 重试 catch | 单次 LLM 调用抛 RateLimit/Timeout 进重试时 | `LLM retry`, model=..., attempt=1, error=... |
| `services/llm.py` 终态 | 3 次重试都失败 / 总超时 10 分钟 | `LLM exhausted`, model=..., last_error=... |
| `workflow/nodes/write_chapter.py` failed 分支 | 章节标 failed 之前 | `chapter failed`, chapter_index=..., reason=... |
| `worker/tasks.py` 顶层 except | task 整体 crash | `task crashed`, task_name=..., trace=... |
| `services/docx_export.py` | pandoc/mermaid 异常 | `docx export failed`, stage=..., stderr=... |

LLM service 里的整合(对应 §11.1):

```python
# services/llm.py 内,在 catch 块加:
from ..core.error_log import append_error
from ..db import session_factory

async def _project_dir(project_id: int) -> Path:
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"), {"p": project_id},
        )
        return Path(row.scalar_one())

# 在 call_llm_stream 的 except 中:
except (RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout) as e:
    last_err = e
    log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
    await append_error(await _project_dir(project_id),
                       f"LLM retry attempt={attempt}",
                       model=model, error_type=type(e).__name__, error=str(e))
    if attempt < settings.llm_retry_max:
        await asyncio.sleep(backoffs[attempt])
        continue
    await append_error(await _project_dir(project_id),
                       "LLM exhausted",
                       model=model, total_attempts=attempt + 1, last_error=str(e))
    raise LLMRetryFailed(str(e)) from e
```

### 19.3 Trace ID 注入

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

### 19.4 健康检查输出示例

```json
{"app": "ok", "db": "ok", "redis": "ok"}
```

不通时 503:`{"app": "ok", "db": "fail: connection refused", "redis": "ok"}`。

---

## 20. 状态机转换图

### 20.1 Project 状态

```
init ──(上传 3 文档,/start)──→ extracting ──(markitdown ok)──→ outlining ──(LLM-1 ok)──→ outline_ready
                                                                                              │
                                                                            /confirm-outline ▼
                                                                                          running ◄────┐
                                                              (并发上限满,排队中)─→ queued        │
                                                                                              │       │ revise
                                                                                              ▼       │
                                                                          (interrupt 暂停)────┴──→ awaiting_review
                                                                                              │
                                                              (跑完所有章节 + assemble) ▼
                                                                                            done
                                                                                              │
                                                          (LLM 失败/超时,且无法 retry)────→ failed
                                                                                              │
                                                                                (用户 abort) ─→ aborted
```

> **queued**(D-T):`/start` 时 Redis ACTIVE_SET 已满 → 项目状态 queued,等待 release_project_slot 自动唤醒(`wake_queued_projects` FIFO);前端显示"排队中,前面 N 个项目"。审核/重试动作占名额满时**不**进 queued,直接 503 + Retry-After 让前端重试(D-AD / FR-1.3)。
> **outline_ready**(D-K):新增 P4 暂停态;前端拉提纲 → 编辑/直接确认 → `/confirm-outline` → 状态进 `running`(进入 awaiting_review 之前一直 running)。

### 20.2 Chapter 状态

```
                                  ┌── revise(retry_count+1) ←─┐
                                  │                            │
pending ──(generate 开始)──→ generating ──(generate 完成)──→ awaiting_review
            ▲                                                  │
            │                          ┌─── approve ────→ approved (terminal)
            │                          ├─── skip ──────→ skipped  (terminal)
            │                          └─── revise ────→ generating (上图回路,
            │                                            到 retry_count > max → 强制 skipped)
            │
            ├── (LLM 3 次失败 / 单章 10 分钟超时) ──→ failed
            │                                          │
            └────(retry_failed_chapter_task)──────────┘
                  ↑ 重置 retry_count=0、last_error=NULL、本轮 ChapterVersion 标 abandoned
```

> max_retry_per_chapter 语义:配置 N 表示"原稿之外允许 N 次重写";到第 N+1 次 revise 自动 skip(`new_retry > N` 时跳)。
> retry_failed 与 revise 是不同动作:revise 走 update_state、retry_failed 是 graph 外重置后从 checkpoint 续跑。

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
| R5 | arq 进程崩了 in-flight job 丢 | workflow task max_tries=1(D-Z) + LangGraph checkpoint 续跑保证恢复;DOCX max_tries=2;keep_result=86400 | M1 |
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
- [ ] `models/` 全 10 张表(§8,含 `Project.encrypted_api_key_snapshot` / `ChapterVersion.abandoned`)
- [ ] `migrations/0001_initial.py`(§9,含 token_usage CASCADE / queued 状态)
- [ ] `db.py`(异步 engine + session_factory)
- [ ] `events/bus.py`(§12)
- [ ] `worker/settings.py` + `worker/lifecycle.py` + `worker/tasks.py`(§10.5,三类任务)
- [ ] `services/concurrency.py` Redis SET + lease token + 唤醒(§10.7,D-T/D-AB)

**Day 2**:
- [ ] `api/projects.py` 创建 / 列表 / 详情 / 删除(级联磁盘目录)
- [ ] `api/projects.py` `/start` 端点 + 真快照 encrypted_api_key_snapshot + Run 创建
- [ ] `api/projects.py` `/documents` 上传 + 日配额聚合校验 + markitdown 抽取
- [ ] `api/projects.py` `/outline` GET + PUT(`/confirm-outline`,resume_review_task)
- [ ] `api/stream.py` SSE
- [ ] `workflow/sync.py` state ↔ DB
- [ ] `workflow/nodes/outline_review.py`(§10.6,interrupt)

**Day 3**:
- [ ] `api/chapters.py` `/review` 端点 → resume_review_task(D-I)
- [ ] `api/chapters.py` `/retry` 端点 → retry_failed_chapter_task
- [ ] `api/projects.py` `/proposal` `/proposal.md`
- [ ] FR-3.10 超时测试(用 fake LLM mock 慢响应)
- [ ] FR-3.9 重试测试(mock 抛 RateLimitError 3 次)
- [ ] FR-4.7 retry 测试:retry_count=0 / abandoned 标记 / ReviewEvent 写入

**Day 4**:
- [ ] `services/api_key_validator.py` + `/api/me/api-key/test` 端点(§15.5)
- [ ] `core/login_throttle.py`(D-Q)+ login 端点重写(§14.6)
- [ ] `core/security_headers.py`(§14.4)+ `main.py` 注册三个 middleware
- [ ] M1 集成测试(§18.3 全部通过)
- [ ] `Dockerfile` + `docker/entrypoint.sh`(D-O)+ `docker-compose.dev.yml`
- [ ] **验收**:`curl` 能跑通完整流程含 SSE 章节流 + 章节 failed + retry 恢复 + queued 排队 + 提纲编辑

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
- [ ] **API Key 真快照**:用户 `/start` 后立刻 `DELETE /api/me/api-key`,工作流仍能跑完(FR-7.6)
- [ ] **提纲确认**:走 P4 编辑路径,LLM-2 收到的 chapters 是用户改过的版本
- [ ] **queued 排队**:并发到 11 时第 11 个项目状态 queued,前面项目 done 后自动 running
- [ ] 章节 failed → retry → 章节 retry_count=0、本轮版本 abandoned=true、生成新版本 → 恢复
- [ ] 章节超时 10 分钟(用 mock 慢 LLM 触发)→ failed 状态正确
- [ ] revise → retry_count + 1;到 max+1 次自动 skip
- [ ] DOCX 含中文 mermaid + 表格,Word 打开无问题
- [ ] DOCX 串行(并发 2 个 docx job → 串行执行,Redis 锁等待第二个)
- [ ] **DOCX 缓存命中**:第一次 POST 生成,第二次 POST 立即返回 `cached: true`
- [ ] **DOCX 下载文件名**:`Content-Disposition` 含 `项目名_技术方案_20260501.docx`
- [ ] 前端 8 个页面无 console error
- [ ] failed 章节红标 + retry 按钮可点

### 安全验收

- [ ] 改密前 428 拦截
- [ ] **登录失败锁 5 分钟**:同 IP 故意失败 5 次 → 第 6 次返 429 + Redis `bid_app:login_lock:{ip}` TTL ≈300
- [ ] **登录成功清零**:失败 4 次后正确登录 → 计数清空,后续可继续登录失败 5 次才锁
- [ ] **全局限流 100/min**:loop 调任意 GET 100 次 → 第 101 次 429
- [ ] **安全头**:`curl -I /` 含 `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `CSP`
- [ ] **上传配额**:同一用户日上传累计触达 500MB → 413
- [ ] API Key 直接读 DB 看到的是 bytes,不是明文
- [ ] 项目 `encrypted_api_key_snapshot` 列也是密文(直接拷贝 ApiKey.encrypted_key 字节;与 ApiKey 行已解耦,用户后续重置 / 删除 ApiKey 不影响)
- [ ] 默认 admin/admin123 + 必须改密 + 改密后 must_change_password=false
- [ ] DashScope banner 登录后显示

### 部署验收

- [ ] `docker compose up -d` 一键起 + healthcheck 全过
- [ ] **entrypoint 顺序**:容器日志先看到 `alembic upgrade head` 通过,才看到 `uvicorn started`
- [ ] **bind mount 生效**:宿主机 `/var/lib/bid-app/projects/` 能直接看到项目文件
- [ ] 6 小时压力(模拟 3 个项目并发跑)无 OOM
- [ ] 凌晨 3 点 cron pg_dump 落到 `/var/lib/bid-app/backups/bid_*.dump`
- [ ] **备份可恢复**:`pg_restore --list` 能列出 10 张表;按 §24.2 恢复成功
- [ ] 容器重启后 in-flight 工作流从 checkpoint 续跑(awaiting_review 状态保持)

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
