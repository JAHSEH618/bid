# 投标技术方案生成器 · 实施 Spec v3.25

> **配套文档**:`REQUIREMENTS.md` v0.12(讲"做什么"),本文档讲"怎么做"。
> **版本**:v3.25 (2026-05-02)。基于 v3.24 修了 4 处测试隔离落地细节:**删 test_docx.py 顶层 `from bid_app.db import session_factory`**(顶层 import 在 monkeypatch 之前求值会绑定旧全局对象,绕过 `_use_test_session_factory`;改注入 fixture,D-DP) / **monkeypatch 列表补全 `write_chapter` / `api.health` + `client` fixture 改依赖 `_use_test_session_factory`**(e2e 测试也走 get_db override,D-DQ) / **`db_engine` 校验改用 `make_url(url).database.endswith("_test")`**(精准解析 + 错误信息删除环境变量误导,D-DR) / **测试库创建策略**(docker compose 加 `init-test-db.sh` 首启创建 `${POSTGRES_DB}_test`;本地非 docker 走 `./scripts/create-test-db.sh`,D-DS)。
> **历史**:v3 修 18 处;v3-pass2 修 9 处;v3.2-3.25 累计 129 处。
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
| **D-M** ⚠️ 由 D-AK 取代 | DOCX 任务**先在 DB 落 `DocxJob(status=pending, arq_job_id=NULL)` 再 enqueue** | 早期方案是 flush 拿 id → enqueue → UPDATE。最终 D-AK 改成 **commit 后 enqueue**(更严格地保证 worker 看得到 row),并加 worker 入口 SELECT 校验。新实现见 D-AK |
| **D-N** | Mermaid 扫描用 **`re.finditer` + 反向 span 替换**,正则容忍 CRLF / 行尾空格 / `~~~mermaid` 围栏 | v2 用 `str.replace` 在重复 mermaid 块时会覆盖第一处之外的全部;反向替换不影响后续 span |
| **D-O** | DB Migration 与服务启动**用 entrypoint 串行化**:容器入口先 `alembic upgrade head` 通过才 `exec supervisord` | supervisord priority 不等于"等到上一个完成";migrate 与 uvicorn 同时启可能导致表不存在时 HTTP 已开始接 |
| **D-P** ⚠️ 由 D-T 取代 | 并发项目上限**双层防护**:业务层 Redis 跟踪 + arq `WorkerSettings.max_jobs=N`(兜底) | 业务侧需要"超限就 queued"的语义。最终实现见 D-T(SET + alive TTL,**非计数器**) |
| **D-Q** | 登录失败锁定**用 Redis 计数 + 锁 key**,不依赖 slowapi 的请求级限流 | FR-6.7 要求"5 次失败后锁该 IP 5 分钟",slowapi 是"匀速速率",语义不一致;改用 `INCR + EXPIRE` 双 key |
| **D-R** | `.env` **单文件方案**:`gen-secrets.sh` 从 `.env.example` seed + sed 替换占位符,生成最终唯一 `.env`,compose 直接读 | docker compose `${VAR}` 插值只读 compose 项目根 `.env` / 宿主 env,**不**读 service 的 `env_file:` 列表;两文件方案中 postgres `${POSTGRES_PASSWORD}` 会插到占位符,起不来 |
| **D-S** ⚠️ 由 D-BQ 扩展 | DocxJob.arq_job_id `nullable=true` + **partial unique index** `WHERE arq_job_id IS NOT NULL`;**(project_id) partial unique 的 status 列表最终为** `('pending','rendering_mermaid','pandoc','finalizing')`(D-BQ 增加 finalizing) | 入队前先 INSERT 拿 id 必须支持空 arq_job_id;并发同项目两次 POST docx 应被 DB 阻断,而不是靠应用层抢锁;finalizing 也是 in-flight,必须进唯一约束防同时两个 job |
| **D-T** | 并发名额用 **Redis SET + 每项目 alive TTL key**,不用计数器;**worker 启动时 reconcile**;唤醒由幂等 `wake_queued_projects(arq_pool)` 函数,不让"占不到名额的任务"留在 arq 里重试。**人工等待不占名额**——task 因 interrupt 退出时 release;`/review` `/confirm-outline` `/retry` 在 enqueue 前重新 `try_acquire`,占不到 → 503 + Retry-After:60 | 计数器在 worker 崩溃时会泄漏正数;SET 与 alive key TTL 配合可识别僵尸条目;worker 进程结束后 heartbeat 必停 → "interrupt 期间持续占名额"物理上不可行;改成"task 周期 = slot 周期"语义干净,且 awaiting_review 时其他项目可以跑 |
| **D-U** | DB commit 后再 enqueue 走**补偿动作**:enqueue 失败 → 回退 DB status + release slot + 503。`wake_queued_projects` 同样:enqueue 失败 → release + 把项目改回 queued | outbox 表对内部 10 用户工具 over-engineering;补偿动作 + reconcile 兜底已足够;reconcile 在 worker 启动时也会清理"无 alive key 但状态 running"的僵尸项目 |
| **D-V** | Redis 用 **`noeviction` 内存策略**,不用 `allkeys-lru` | 同一 Redis 同时承载 arq 队列、active set、login lock、event pub/sub、limiter 计数。LRU 策略下,内存压力大时 Redis 会**默默驱逐任意 key**,可能让 arq 任务、并发名额、登录锁全部消失,触发难定位的 silent 故障。`noeviction` 让写入在内存满时显式失败,我们在监控 / OOM 风险下能立刻发现 |
| **D-W** | DSN(`DATABASE_URL` / `LANGGRAPH_DSN`)由 **`config.py` 从 `POSTGRES_USER/PASSWORD/HOST/PORT/DB` 字段拼装**,不在 `.env` 写带 `${VAR}` 的派生值 | docker compose 的 `env_file:` **不**对值做变量展开;容器内 `pydantic-settings` 读 OS env 也不展开。`.env` 里 `DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@...` 实际进容器是字面值。改在 `config.py` 用 property 拼,既避免展开问题,又支持密码里含 `@/&` 等需要 URL-quote 的字符 |
| **D-X** | 项目级错误日志写到 **`{project_dir}/errors.log`**(NFR-5 要求),与 stdout structlog 并存;**异常路径写完整 `traceback.format_exc()` 而不是 `repr(e)`** | 用户排查"为什么这个项目挂了"时,需要看项目目录里的错误而不是从 docker logs 海量 stdout 里搜索;NFR-5 明确要"完整堆栈" |
| **D-Y** | ALIVE_KEY **双 TTL**:API try_acquire 设 RESERVE_TTL=300s(预留 enqueue→worker 启动的延迟窗口);task 进入 heartbeat 后续租到 ALIVE_TTL=60s | v3.2 用单 60s TTL,arq 排队 / worker 重启延迟时 ALIVE_KEY 过期 → reconcile 误清 ACTIVE_SET → 并发统计失真。两段 TTL 让"reservation"与"alive"语义分开 |
| **D-Z** ⚠️ 由 D-AY 收紧 | workflow 三类 task **`max_tries=1`**;DOCX task **原 `max_tries=2` → 现 `max_tries=1`**(D-AY) | LLM 失败后 task 抛异常,arq 默认会自动重试整个 task,这会:① 绕过 API 端 `try_acquire`(违反 D-T 名额管理);② 与 FR-3.9/FR-4.7 的"用户手动 /retry"语义冲突;③ 浪费 LLM 调用。崩溃恢复改靠 LangGraph checkpoint(用户下次 /retry 时从最近成功节点续跑)。DOCX 原打算自动重试一次但需要 `ctx['job_try']` 配合"只在最后一次标 failed"逻辑,与现有"第一次失败立刻 UPDATE failed"实现冲突——第二次重试进来 SELECT 看到 done/failed 直接退出,等于没真重试。统一为 1 次更简单;失败由用户从 P6 点"重新生成"触发新 DocxJob |
| **D-AA** | `WorkerSettings.max_jobs = max_concurrent_projects + 2`,给 DOCX task 留余量;DOCX Redis 锁 `blocking_timeout` 缩短到 120s 防止"等锁的 DOCX 占满 worker job 槽" | DOCX 串行锁让多个 DOCX 任务在 arq worker 里阻塞等待,如果 max_jobs 等于项目并发上限,等锁的 DOCX 把 worker 槽全占,新 workflow task 入队后排在 arq 队列里饿死 |
| **D-AB** | slot **lease token**:`try_acquire` 返回 uuid token 存到 ALIVE_KEY 值;task 入口 `ensure_project_slot(token)` 校验;`heartbeat`/`release` 用 Lua CAS 仅当 token 匹配才操作 | 仅靠 RESERVE_TTL 不能消除时序竞态:worker 阻塞 / 排队 > 5 分钟时 ALIVE_KEY 过期 → reconcile 把 ACTIVE_SET 清掉 → task 后续启动时**不知道**自己已被踢,继续跑导致并发超限。token 让 task 能精确判断"我还持有 slot 吗",失败可重新 acquire 或退出 |
| **D-AC** ⚠️ 由 D-AZ 收紧 | `ReviewEvent` 在 **worker 入口**写,不在 API 端写;**仅 revise 切 generating**,approve/skip 保持 reviewing 由 update_state 节点切到 approved/skipped(D-AZ) | v3.3 把 ReviewEvent 写在 enqueue 后,enqueue 成功但 commit 失败时事件没写。改在 worker 入口写,与 graph 真正执行同生同灭。v3.8 一律切 generating 把 approve/skip 误归类成"正在写章节",D-AZ 修正 |
| **D-AD** | API 端审核/重试加 **`SELECT ... FOR UPDATE` 行锁** + 状态校验 | 防止过期页面 / 双击 / 多用户同时审同一章节导致重复提交;状态校验确保只有 `awaiting_review` 章节能被 review、`failed` 章节能被 retry |
| **D-AE** | errors.log 用 **JSONL 格式**,traceback 作为单行 JSON 字符串字段 | v3.3 设计的 key=val 平文本假设"每行 < PIPE_BUF 4KB 不交错",但 traceback 多行长字段打破假设。JSONL 把多行字段编码成单行 JSON,单次 write 完成,跨进程 append 不交错 |
| **D-AF** | `try_acquire_project_slot` 三态返回:`token` / `None+full` / `None+already_active`;**仅 added==1 时**才 SET ALIVE_KEY;`/start` 必须校验 `project.status=='init'` | v3.4 Lua 在 `size < max` 内不区分 added=0/1 都 SET ALIVE_KEY,会覆盖正在跑 task 的 token,task 入口 `ensure_project_slot` 仍能通过(因为新 token 是同一进程 acquire 写的),但物理上是两份"持有人";严格化后 already_active 直接拒绝,防止重复启动 |
| **D-AG** | **三层 reconcile**:① try_acquire / wake_queued 内置 lazy SREM 没 alive key 的成员 ② arq cron 每分钟 `reconcile_periodic` ③ worker startup 全量扫;heartbeat 返回 bool,失败抛 `SlotLost` 让 task 主动中止 | v3.4 reconcile 仅 worker startup 跑,worker 不重启 + heartbeat 异常时 ACTIVE_SET 永久泄漏;三层 reconcile 让僵尸条目在不同时间窗口都被清理;heartbeat 返回 bool 让"被清理后的孤儿 task"主动停 |
| **D-AH** | worker task **拿到 token 后立即进 `try/finally`**,所有 DB 写入(ReviewEvent / chapter reset)都放 try 块内 | v3.4 把 ReviewEvent 写在 try 块外,DB 异常时漏 release_project_slot;统一进 try 后,任何异常路径都保证 slot 释放 |
| **D-AI** | chapter status 加 **`reviewing` / `retrying`** 两个中间态;`/review` `/retry` 在 API 行锁内切到中间态;worker 入口接管(切到 generating / pending)并由 update_state 落最终态 | v3.4 行锁在事务 commit 后释放,章节状态仍是 awaiting_review / failed,第二个请求能再次过校验导致重复入队;中间态语义是"决定已下,等 worker 应用",前端看到这个状态可禁用按钮 |
| **D-AJ** | `WorkerSettings.functions` 直接放**装饰后的函数对象**,不放字符串路径 | 字符串路径需要 arq 通过 import 后再发现 `__arq_function__` 之类属性,各版本行为不完全保证;直接传函数对象消除这个不确定性。配 unit test 启动期断言 `start_workflow_task.max_tries == 1` |
| **D-AK** | `DocxJob` 顺序:**先 commit pending 行 → 再 enqueue → 再 update arq_job_id**;`generate_docx_task` 入口 SELECT 校验 row 存在 | v3.4 是 flush→enqueue→commit;enqueue 成功但 commit 失败时,worker 拿到 docx_job_id 但 DB 没行,worker 会崩或卡住。先 commit 让 row 可见;enqueue 失败用补偿动作把 row 标 failed |
| **D-AM** | heartbeat 协程**只设 lost_event,不能打断主 graph**;主循环显式 `if lost_event.is_set() or not await ensure_project_slot(...): raise SlotLost` | v3.5 实现里 heartbeat 失败只 cancel_event.set(),但 `graph.astream()` 已经在跑,asyncio 不能"打断"协程外层循环;主循环必须**显式**在每个 step 后校验,token 失效时 raise SlotLost 退出。`SlotLost` 是合法终态(不算业务错误,不写 errors.log) |
| **D-AN** | Lua acquire **不做 SREM**,改为"数 alive 成员个数";SREM + DB 同步统一交给 Python `reconcile_active_projects()`(由 cron / startup 调用) | v3.5 在 Lua 里 SREM 僵尸成员,但这绕过了 cron `reconcile_periodic` 的 DB 同步——僵尸 SET 被静默清后,Project 状态卡在 running/extracting/outlining 没人改。改成"acquire 只看 alive 数不动 SET",所有 SREM 都伴随 DB 同步,不漏单 |
| **D-AO** | `/review` `/retry` 把"切中间态 + acquire + enqueue"全部包在外层 `try/except`,任何异常(包括 Redis / SQLAlchemy / arq 抛错)都补偿章节状态回 awaiting_review/failed | v3.5 只 catch HTTPException;Redis 调用抛 `ConnectionError` 时章节会卡在 reviewing/retrying 状态,前端禁用按钮永远点不开;补偿全包后任何异常都回滚 |
| **D-AP** | `wake_queued_projects` **不预判 SCARD**,直接调 `try_acquire_project_slot`,内部按 alive count 判断 | SCARD 把僵尸成员算入,与 D-AN "alive count = 真容量"冲突——僵尸存在时 SCARD 显示满,wake 不唤醒;实际 alive_count < max,有可用名额。删 SCARD 后 wake 真实反映容量 |
| **D-AQ** | `try_acquire` 区分 stale(`-2`)与真 already_active(`-1`):Lua 检查 SISMEMBER 后再查 ALIVE_KEY 存在性;stale 时 Python `_evict_stale_project` 同步移除 + DB 标 failed,然后递归一次 acquire | v3.6 SISMEMBER==1 直接返回 already_active,但 ALIVE_KEY 已过期的 stale 成员被卡住,直到 cron 60s 后才清。stale 自愈 + 维持 D-AN "SREM 必须伴随 DB 同步"原则(只是绑定位置在 Python) |
| **D-AR** | chapters 加 `processing_started_at`;cron `cleanup_stale_chapters` 每分钟扫 `reviewing/retrying` 状态超 60s 的章节,回滚到 awaiting_review/failed | API commit 中间态后进程崩溃(arq 没拿到、worker 重启等)会让章节卡中间态;前端禁用按钮永远点不开。超时回滚让用户能重新提交审核或重试 |
| **D-AS** ⚠️ 由 D-AY / D-BH / D-BQ / D-BY 扩展 | cron `cleanup_stale_docx_jobs` 每 5 分钟扫 `DocxJob.status` 在 **任意 in-flight 状态**(`pending` / `rendering_mermaid` / `pandoc` / `finalizing`)超 30 分钟的标 failed;**finalizing 加 repair 分支**(文件已存在 → 修复成 done) | v3.7 只覆盖 pending。最终实现 D-AY 加全 in-flight 范围、D-BH 改用 updated_at、D-BQ 加 finalizing、D-BY 加 finalizing repair(rename 成功但 done UPDATE 异常时文件已就位,标 done 比标 failed 更准) |
| **D-AT** ⚠️ 由 D-AW / D-AZ 扩展 | worker `SlotLost` 分支**幂等回滚章节状态**;最终形态由 D-AW(覆盖 reviewing/retrying/pending/generating)+ D-AZ(按 decision 区分:approve/skip 回 awaiting_review,revise 标 failed)定义 | v3.6 SlotLost 只 release slot,章节卡中间态;补偿与 D-AR cron 互补——cron 兜底 60s 阈值,SlotLost 立即触发 |
| **D-AU** | LLM-2 章节生成失败语义化:`services/llm.py` 加 `ChapterGenerationFailed`,`write_chapter` 节点把 `LLMRetryFailed` / `Timeout` 包成它再 raise;worker 三类 task 加 `except ChapterGenerationFailed`:**不写 errors.log**(节点已写 chapter.last_error)/ **不 raise**(arq 不重试)/ project 切 `awaiting_review` | v3.7 章节 3 次失败后裸 raise,被 task 顶层 `except Exception` 捕获 → `_set_project_status(project, 'failed')`,违反 FR-3.9/FR-4.7 "工作流暂停在该章节,等用户手动 retry" 的语义。语义化异常让"章节级失败"和"task 真崩溃"在 except 链里精准分流;LLM-1 / 抽取阶段失败仍走 generic Exception → project failed(那才是真项目级失败)|
| **D-AV** ⚠️ 由 D-BB / D-BF 取代 | `cleanup_stale_chapters` 先取活跃 project 排除;`/review`/`/retry` 异常补偿清 `processing_started_at`(基础动机) | v3.7 cleanup 阈值 60s 与 RESERVE_TTL=300s 冲突。**最终实现**:D-BB 用真 alive ids(SET ∩ ALIVE_KEY)+ typed array CAST;D-BF 把 generating 也加进 cleanup(超时 15min)|
| **D-AW** ⚠️ 由 D-AZ / D-BI 收紧 | SlotLost 补偿统一抽 `_slot_lost_compensation(project_id, run_id, current_chapter_id, action[, decision])`:覆盖 `reviewing/retrying/pending/generating`,run 下任意 `generating` 也回滚,RETURNING 判定 → project 切 awaiting_review 或不动 | v3.7 D-AT 只回滚"参数指向的章节",resume 跑下一章 SlotLost 时下一章 chapter_id 不在参数里。**最终实现**:D-AZ 按 decision 区分(approve/skip 回 awaiting_review,其它 failed);D-BI 同步把对应 ReviewEvent 标 aborted 防前端误显示 |
| **D-AX** | `wake_queued_projects` 中 `try_acquire` 返回 `already_active` / `stale_evicted` / 或 run 缺失 → **立即 UPDATE projects.status='failed'**(不再仅 `continue`);返回累计 woke_count 而非 0 | v3.7 异常 queued 项目 `continue` 后,`SELECT FOR UPDATE SKIP LOCKED` 是事务级行锁,事务 commit 后释放,**下一轮循环又取到同一个 project**,wake 死循环占着 WAKE_LOCK 30s。立即标 failed 让 SKIP LOCKED 跳过该行;woke_count 让调用方与监控能区分"队列空"与"全满" |
| **D-AY** ⚠️ 由 D-BQ / D-CF 收紧 | DOCX task `max_tries=2 → 1`(与 D-Z 其他任务一致);`cleanup_stale_docx_jobs` 覆盖**所有 in-flight 状态**;**最终 in-flight 状态列表**:`pending` / `rendering_mermaid` / `pandoc` / `finalizing`(D-BQ 加 finalizing,D-CF 同步注释/R12;D-CG 的 `invalidated` 是**显式终态**不算 in-flight) | `max_tries=2` 与"第一次失败立刻 UPDATE failed"实现冲突。统一 1 次 + cleanup 全 in-flight 范围才是 R12 的真正闭环 |
| **D-AZ** | `_slot_lost_compensation` 接 `decision` 参数;**approve/skip → 章节回 awaiting_review**(决策有效但 update_state 没跑完,让用户重新提交一次);**revise / retry / start → 章节标 failed**(可能已开始重写);worker 入口对 approve/skip 也**不再切 generating**,只 revise 才切 | v3.8 worker 入口无条件把 reviewing → generating,但 approve/skip 实际不会"重写",`generating` 这个语义只对 revise 成立;同时 SlotLost 把 reviewing/retrying/pending/generating 全部标 failed 对 approve/skip 是过度补偿。按 decision 分流后语义对齐:状态机能讲清"approve/skip 走得是哪条路"|
| **D-BA** | `_fail_project_and_run(project_id, run_id, error)` helper:三个 worker 顶层 generic exception 时**同时**写 Project.status='failed' 与 Run.status='failed' + finished_at + error;ChapterGenerationFailed / SlotLost **不动 Run**(章节级失败工作流仍可恢复)| `models.Run.status` 字段定义了 `running/done/failed/aborted` 但 v3.8 里 task crash 只写 Project,Run 永远停 running,审计 / 恢复看不到工作流真状态。同步后 Run 表能精准反映"本次 thread_id 执行结果",且 Run.error 比 errors.log 更便于查询(SQL 而不是文件检索)|
| **D-BB** | `cleanup_stale_chapters` 用 `get_alive_project_ids()`(SET ∩ ALIVE_KEY 实存)排除而不是裸 `SMEMBERS ACTIVE_SET`;SQL 改 `r.project_id <> ALL(CAST(:active_ids AS int[]))` 显式 typed array | v3.8 直接排除 SET 全体,但 stale 自愈(D-AQ)有时间窗口,SET 里可能短暂含失效成员 → 那些项目的章节也被"误信任"跳过。改用真 alive 列表更精准。SQL CAST 防 SQLAlchemy/asyncpg 把 Python `[]` 错推断成 `text[]` 之类;空列表也安全(`<> ALL(ARRAY[]::int[])` 恒 true)|
| **D-BC** | `test_docx_task_has_max_tries_1` 替换 v3.7 留下的 `_2`;断言 `mt == 1` | D-AY 改了实现但忘改测试,启动期断言会假报失败;同步修正 |
| **D-BD** | `export_docx(... on_stage=callable)` 回调,`_export_docx_inner` 在 mermaid 完成 / pandoc 开始时调 `await on_stage("pandoc")`;`generate_docx_task` 实现 `_update_stage` 写 DocxJob.status='pandoc' | v3.8 定义了 `pandoc` 状态(模型 + cleanup SQL 都覆盖),但 task 实际只写到 `rendering_mermaid` 就直接调 export_docx,pandoc 状态从未被写入 → cleanup 永远扫不到真正卡 pandoc 的 job(因为它们仍是 rendering_mermaid),partial unique index 的语义也对不上。回调让生命周期和字段定义对齐,且 `_export_docx_inner` 内部 mermaid → pandoc 切换点是唯一精确锚点 |
| **D-BE** ⚠️ 由 D-BG 扩展 | `services/llm.py` 主代码直接在 `call_llm_stream` 的重试 catch 块调 `_write_llm_error(project_id, ...)`,落 errors.log;§19.2 不再重复实现,只留对照说明 | v3.8 主代码 §11.1 只写 `log.warning`,errors.log 写入散在 §19.2 的"调用点"附录里,落地时极易漏写;FR-3.9 明确要求"重试日志记录到 errors.log"。合并到主代码避免实现分裂,保持需求 ↔ 实现一一可追溯 |
| **D-BF** | `write_chapter` 节点切 generating 时一并 SET `processing_started_at=NOW()`;`cleanup_stale_chapters` 加 `generating` 分支(超时 = single_chapter_timeout + 5min margin = 15min);`ix_chapters_processing` 索引 partial WHERE 也覆盖 generating | v3.9 cleanup 只扫 reviewing/retrying。worker 进程被 SIGKILL/OOM 直接死时不会进 SlotLost 分支,generating 章节没有任何兜底自愈机制,会永久卡住。配合 D-BB 的 active_ids 排除,15 分钟阈值在"LLM 单章 600s + 调度容错"和"用户能合理等到回滚"之间取平衡 |
| **D-BG** | `call_llm_stream` / `call_llm_json` 都在 `asyncio.timeout` 外层捕 `TimeoutError`,写 `LLM total timeout` errors.log 后 raise `LLMTimeoutExceeded`;`call_llm_json` 重试链也调 `_write_llm_error`(与 stream 等价对齐) | v3.9 errors.log 仅覆盖 stream 模型的网络/限流重试。① JSON 模型(LLM-1 提纲 / LLM-3 可视化)的重试只写 structlog 不进项目级日志;② 外层总超时(FR-3.10 600s 兜底 / JSON 120s)从 asyncio.timeout 抛 TimeoutError 不会进重试 catch,errors.log 直接漏。统一外层 catch + helper 调用,消除三个盲区 |
| **D-BH** ⚠️ 由 D-BQ / D-BX 收紧 | `DocxJob` 加 `updated_at`(server_default + 显式 SQL SET);cleanup 用 `updated_at < NOW() - 30min`;终态 UPDATE 加 WHERE 防覆盖。**最终实现** D-BQ 把"防覆盖"扩展到全阶段(`finalizing` 状态 + atomic rename 之后才 done);D-BX 把每次阶段切换都加状态前置 + rowcount 守护 | v3.9 cleanup 用 `created_at` 判超时,但 task 进 rendering_mermaid 后会等 export_docx 内的串行锁(可能 > 30min);cron 标 failed 后 task 仍可能拿到锁继续跑并 UPDATE done,**覆盖** failed 状态导致数据腐烂。最终通过 finalizing + 阶段守护双层防护闭环 |
| **D-BI** | `ReviewEvent` 加 `aborted` Boolean 默认 false;`_slot_lost_compensation` 在 approve/skip 分支(章节回 awaiting_review 时)同步 `UPDATE ... SET aborted=true` 标记最近一条 pending ReviewEvent;前端"最近审核人/决策"查询加 `WHERE aborted=false` | v3.9 worker 入口写 ReviewEvent 后 graph 半路 SlotLost,approve/skip 章节回 awaiting_review 但 ReviewEvent 仍存,前端"上次审核人"按最近事件取会误显示一个**未真正生效**的决策(用户以为已审,实际还要重审)。aborted 字段保留审计原意("做过这个动作")同时区分"是否生效",前端过滤即可消除歧义 |
| **D-BJ** | D-AV / D-AW 标 ⚠️ "由 ... 取代",描述压缩到核心动机 + 指向最终决策的指针 | 决策表里同名机制有多版迭代痕迹时,落地工程师可能照旧版描述实现。明示"最终口径见 D-XX"减少误读 |
| **D-BK** | 所有把章节切到 `generating` 的位置都同步 SET `processing_started_at=NOW()`:`update_state` revise 分支(`sync_chapter_to_db`)+ `resume_review_task` revise 分支(原始 SQL)+ `write_chapter` 节点(已在 D-BF 加) | v3.10 D-BF 只在 `write_chapter` 写,但章节先经 update_state 或 worker 入口切 generating,write_chapter 实际执行前还有调度窗口。worker 在该窗口被 SIGKILL → 章节卡 generating 且 `processing_started_at IS NULL`,cron `< NOW() - INTERVAL` 比较 NULL 永远为 NULL → 不会被回滚。统一在所有切 generating 处写时间戳,保持"generating ⇒ processing_started_at NOT NULL"不变量,cleanup 才能闭环 |
| **D-BL** | `cleanup_stale_chapters` RETURNING 带回 project_id;对回滚 generating 章节的 project,UPDATE projects SET status='awaiting_review' WHERE status IN ('running','extracting','outlining','failed') | v3.10 cleanup 只改 chapter,不动 project。worker SIGKILL 后 reconcile_periodic 会把 project 标 failed,即使 chapter 后续被本 cron 标 failed 用户能 retry,但 project=failed 阻止了交互(前端通常按 project.status 决定是否暴露 retry)。把 project 切 awaiting_review 让"chapter 失败 → 用户 P5 retry"路径与 SlotLost 路径行为一致。允许从 failed → awaiting_review 反向流转,因为现在确定有 failed chapter 可恢复 |
| **D-BM** | `_slot_lost_compensation` approve/skip 分支先 `r1.fetchall()` 拿命中行;**仅当非空才标 ReviewEvent aborted**;空时说明 update_state 已落 approved/skipped,决策真生效,不再撤销 | v3.10 无条件标 aborted,但 approve/skip 决策可能已经被 update_state 节点真落库到 chapter(approved/skipped),回滚 SQL 命中 0 行;此时把 ReviewEvent 标 aborted 是错的:那条决策真生效了,审计应该保留。rowcount 守卫让"标 aborted"和"chapter 真被回滚"严格同步 |
| **D-BN** ⚠️ 由 D-BQ 收紧顺序 | `export_docx` / `_export_docx_inner` 写到 `proposal.{job_id}.tmp.docx`(tmp 路径);`generate_docx_task` 做 atomic rename。**最终顺序** D-BQ:抢占 finalizing → rename → done(原 D-BN 是 done → rename,中间崩溃 DB done 但文件不存在);rename 失败兜底标 failed + unlink | v3.10 export_docx 直接写终态 `proposal.docx`。cron 把 DocxJob 标 failed 后 task 仍可能写完文件;接着 GET /proposal.docx 看到 file exists 直接返回 cached,把不完整产物展示给用户。tmp + finalizing + atomic rename 三件套让"DB 成功 ⇔ 终态文件存在"严格同步 |
| **D-BO** | `call_llm_json` JSON 解析失败 raise `LLMRetryFailed` **之前**也调 `_write_llm_error(... attempt=...)`;最终重试用尽时落 `LLM exhausted`(JSON 路径) | v3.10 D-BG 只覆盖了网络/限流错误。JSON parse 走另一条 raise 路径,errors.log 只在 ServiceUnavailable 等情形写,LLM-1 / LLM-3 因为 JSON 解析失败而最终 fail 时项目级日志为空。补完后 D-BG "call_llm_json 重试链都写 errors.log" 完全成立 |
| **D-BP** | `_build_update_sql(fields)` 实际实现:`_CHAPTER_SYNC_ALLOWED` 白名单(`status` / `final_text` / `last_error` / `retry_count` / `processing_started_at`),不在白名单或 key 不是 Python 标识符 → `raise ValueError`;set 子句拼字段名,值仍走 `:k` 绑定 | v3.10 `sync_chapter_to_db` 调了 `_build_update_sql` 但文档没给实现;落地时调用方可能写 `chapter_id=...`(列不存在)或拼任意字段名,前者静默失败、后者潜在拼出错误 SQL。白名单让错误在调用时立即暴露;字段名不绑定参数(:k 是值绑定),所以白名单 + isidentifier 双重过滤是必要的 |
| **D-BQ** | DOCX 引入 `finalizing` 状态:`rendering_mermaid → pandoc → finalizing(抢占,带 WHERE)→ tmp_path.rename → done(WHERE status='finalizing')`;partial unique index、cleanup、in-flight failed 兜底 SQL 都包含 finalizing;download 端 done 但文件不存在 → 自动 UPDATE failed | v3.11 task 是"先 UPDATE done 再 rename",中间崩溃 → DB done 但 `proposal.docx` 不存在,download 端 409 卡死。引入 finalizing 后"finalizing → rename → done"是可恢复中间态:rename 前崩 → 文件不存在但 DB 仍 finalizing,被 cleanup 标 failed;rename 成功后 done UPDATE 一次性原子完成。download 端的兜底是文件层面被人为删除等 catastrophic 情形的最后一道 |
| **D-BR** | `_module_lock` 改成 `await asyncio.wait_for(_module_lock.acquire(), timeout=120s)`,超时 raise `TimeoutError` → task 标 failed;原 `async with _module_lock` 没超时 | `async with` 等待无限,多个 DOCX task 入队就全部占着 arq job slot 等本地锁。即便 D-AA 把 `max_jobs = max_concurrent_projects + 2`,DOCX 数量超过 +2 仍 starve workflow。timeout 让超额 DOCX 主动失败(用户可重试),不再无限制占 worker slot;120s 与 Redis blocking_timeout 对齐 |
| **D-BS** | retry worker 切 pending 时同步 SET `processing_started_at=NOW()`;`cleanup_stale_chapters` 加 `pending AND processing_started_at IS NOT NULL` 分支(60s 阈值);`ix_chapters_processing` partial WHERE 加 pending;cleanup 切 Project=awaiting_review 也覆盖 pending → failed 的 project | v3.11 retry worker 把 chapter retrying → pending 后 `graph.astream(None)`。如果 worker 在切 pending 后 / graph 启动前崩溃,chapter 卡 pending 永久(cleanup 不扫 pending)。NOT NULL 守护防止初始未跑过的 pending 章节被误回滚——只有 retry 路径才会写 processing_started_at |
| **D-BT** | resume worker 入口 `s.flush()` 拿 `review_event_id`,传给 `_slot_lost_compensation`;补偿端 `UPDATE review_events SET aborted=true WHERE id=:rev_id`,而不是按 chapter_id 取最近一条 | v3.11 D-BI 按"最近一条 NOT aborted ReviewEvent"标 aborted;若补偿延迟期间用户又提交新审核,新事件会被错标。精确 id 让补偿与具体事件一一对应,无论补偿何时跑都正确 |
| **D-BU** | REQUIREMENTS.md v0.11:Chapter 加 `processing_started_at`,ReviewEvent 加 `aborted`,DocxJob 加 `updated_at` 与 `finalizing`(均标"实现层");R12 文字 `created_at > 30min` → `updated_at < NOW() - 30min`,同时补 finalizing/atomic rename 链路说明 | spec 实现层加了字段但 REQUIREMENTS 数据模型表没同步,上下游契约不一致;且实现层字段不应被外部 API 误以为是契约的一部分,需明确"实现层内部"。R12 文字按 `< NOW() - 30min` 才符合"超过 30 分钟未更新"的真实语义,`> 30min` 是反向比较 |
| **D-BV** | REQUIREMENTS.md v0.12 把 FR-5.8"其他 docx 请求排队"改成"短时间排队(120 秒)等不到锁就失败,前端可重试";不做无界排队 | 原文字暗示无界排队,但 spec D-BR 实际是 120s 超时即失败(防 DOCX 占满 worker slot 饿死 workflow)。需求服从实现,语义对齐 |
| **D-BW** | 新增 `GET /api/projects/{id}/docx-job/{docx_job_id}` 端点(REQUIREMENTS §9 列了但 v3.12 缺);返回 `{status, stage, error, ...}`;**`finalizing` 内部状态对前端映射成 `processing`**,`pending`/`rendering_mermaid`/`pandoc` 同样映射 `processing`;done/failed 直传 | REQUIREMENTS API 表 v0.10 起列了 `/docx-job/{job_id}`,但 spec §15.3 一直只实现 POST + GET 下载;前端无法轮询进度。`finalizing` 是 D-BU 标"实现层"的内部态,API 不应直接暴露(否则前端要为它写一套 UI 文案),映射成 `processing` 是对外语义最干净的处理 |
| **D-BX** | `generate_docx_task` 阶段切换全部加 WHERE 状态前置 + rowcount 守护:`pending → rendering_mermaid` `WHERE status='pending'`;`rendering_mermaid → pandoc`(via `_update_stage`)`WHERE status='rendering_mermaid'`;rowcount==0 → raise `_StaleJob` → 上层 unlink tmp 并 return | v3.12 阶段切换都是裸 `UPDATE WHERE id=:i`,cleanup 把 job 标 failed 后,task 仍能把它改回 rendering_mermaid / pandoc(SQL 不带状态前置)。每阶段加 WHERE 让"cleanup 已 failed → 后续阶段都跳过"严格成立;`_StaleJob` 做信号,task 收到后清半成品 tmp 文件并安静退出,不再 raise 给 arq |
| **D-BY** | `cleanup_stale_docx_jobs` 加 finalizing repair pass:扫所有 finalizing 行,文件存在 → repair 成 done,文件不存在 + 超时 → 标 failed;POST `/proposal.docx` 命中 cached 文件前也修复"finalizing 但文件就位"的孤儿 | v3.12 D-BQ 让 task 顺序变成"finalizing → rename → done",但 rename 成功后 done UPDATE 仍可能因 DB 异常失败(网络抖动 / pool 耗尽),DB 卡 finalizing,文件已存在。后续 cleanup 直接标 failed 是错的——产物完整应当 repair 成 done。POST 端的额外 repair 是防御性兜底:用户在 cleanup cron 跑之前已经触发新 POST 也能立刻看到正确状态 |
| **D-BZ** | `ix_chapters_processing` partial WHERE 改成 `status IN ('reviewing','retrying','generating') OR (status='pending' AND processing_started_at IS NOT NULL)` | v3.11 条件 `status IN ('reviewing','retrying','pending','generating')` 让所有初始 pending 章节(processing_started_at IS NULL)也进索引,但 cleanup 的 pending 分支带 NOT NULL 守护,初始 pending 永远扫不到。索引收紧到"真正会被扫到的行"才精准 |
| **D-CA** | D-S / D-AS / D-BH / D-BN 等老决策行加"⚠️ 由 D-BQ / D-BX / D-BY 收紧"标记;描述压缩到核心动机 + 指向最终决策 | 决策表里同名机制有多版迭代痕迹时,落地工程师可能照旧版描述实现。D-BQ 之后 finalizing 流程已显著重塑 DOCX lifecycle,旧描述里的字段列表(in-flight 状态、unique 列)漏掉 finalizing 容易让实现者以为不需要 |
| **D-CB** | `_export_docx_inner` 删除 `on_stage("pandoc")` 周围的 `try/except`,让 `_StaleJob` 透传到 `generate_docx_task` 顶层 catch | v3.13 D-BX 的关键设计是 on_stage rowcount==0 → raise `_StaleJob` → task 退出 + unlink tmp;但 v3.13 同时保留了"on_stage 异常吞掉只 log"的旧 catch,这两个直接冲突 — _StaleJob 会被吞,pandoc 仍然继续跑,阶段守护失效。on_stage 内部 rowcount 路径已显式 log.warning,无需再外层兜底 catch;一致性大于"最大化容错" |
| **D-CC** | POST `/proposal.docx` 返回 `{docx_job_id, arq_job_id, cached}`;GET 路径参数用 `docx_job_id`;REQUIREMENTS §9 同步成 `/docx-job/{docx_job_id}` | v3.13 POST 返回里 `job_id` 实际是 arq job id,但 REQUIREMENTS 路径写 `/docx-job/{job_id}`,GET 实际查的是 DB PK(`docx_job_id`)。前端按 REQUIREMENTS 实现就会拿 arq job id 去查 DB,404。改名后语义对齐:`docx_job_id` 是用户域(轮询用),`arq_job_id` 是基础设施域(排查用) |
| **D-CD** | `get_docx_job` 端点查到 `status='finalizing'` 且 `proposal.docx` 文件存在 → inline `UPDATE done` 再返回;repair 后用新行覆盖 `row` 避免下方仍按 finalizing 映射 | v3.13 D-BY 把 finalizing repair 放在 cleanup(5 分钟周期)和 POST(用户主动触发);但前端最常走 GET 轮询,如果 task 已 rename 但 done UPDATE 失败,GET 会卡 processing 直到 cron 跑。inline repair 把延迟从 5min 缩到一次轮询;repair 不命中时回退到原映射逻辑,无副作用 |
| **D-CE** | `generate_docx_task` 终态 `UPDATE done` 检查 `result.rowcount`:0 时 SELECT 当前状态分类处理 — 已 done(被 D-BY/D-CD 抢先 repair)→ log info 静默;其它 → log warning + 返回 `{"status": "stale", "output_path": str(final_path)}`;不删 final_path | v3.13 done UPDATE 后无 rowcount 检查,DB 状态可能已被并发 repair 改走;task 仍返回成功,数据真相依赖 DB 实际状态。明确 rowcount 分类:"已 done"是合法并发,静默;"其它"说明并发 repair 把它标 failed(理论上 D-BY 不会,但保留 warning + 返回 stale),给上层观测点 |
| **D-CF** | DocxJob 模型注释里 `partial unique` 状态列表加 `'finalizing'`(D-BQ 之后 finalizing 也是 in-flight,但 v3.13 注释漏);R12 风险行扩展到 D-BX/D-BY/D-CD/D-CE 全链路,描述 finalizing 的"repair-or-fail"双路径而不是笼统 failed | 模型注释和 §9 migration SQL 不一致是落地实现层面的常见错源:实施者通常先看 model 注释,容易漏掉 finalizing。R12 文字若停留在"in-flight 超时标 failed",容易让实现者以为 cleanup 只标 failed 一条路径,把 D-BY 的 repair 实现简化成普通 failed 标注 |
| **D-CG** ⚠️ 由 D-CM 扩展 | DocxJob.status 加 `invalidated`(显式终态,不算 in-flight);`assemble` 节点写 `proposal.md` 后同步 `unlink proposal.docx` + `UPDATE docx_jobs SET status='invalidated', output_path=NULL` 作废本 project **所有未终结的 DocxJob**(D-CM 把作废范围从 done 扩到 done + 全 in-flight `pending/rendering_mermaid/pandoc/finalizing`);GET /docx-job 把 invalidated 直传给前端;REQUIREMENTS FR-5.7 + DocxJob 模型表同步 | REQUIREMENTS FR-5.7 承诺"DOCX 缓存直到 markdown 重新生成才失效",但 v3.14 前 assemble 既不删旧文件也不动 DocxJob,POST 看 file exists 直接命中旧产物。invalidated 显式终态让作废语义可见(保留审计 vs 直接删行)。**最终作废范围由 D-CM 定义**:不只 done,还包括 in-flight 任务,否则旧 task 后续完成会写"基于旧 markdown 的产物"成 done |
| **D-CH** | 补 5 个 DOCX 状态机回归测试:① on_stage rowcount=0 → _StaleJob 抛 + pandoc 不跑(D-BX/D-CB);② GET /docx-job repair finalizing 当文件存在(D-CD);③ arq_id 当 docx_job_id 查 → 404(D-CC);④ done UPDATE rowcount=0 但已 done → 静默 ok(D-CE);⑤ assemble invalidate 旧 done(D-CG)。fixture: docx_job_factory / project_factory / run_assemble_node | DOCX 状态机经过 D-BQ/D-BX/D-BY/D-CD/D-CE/D-CG 已经是最复杂、最容易回归的子系统,但 v3.13 测试清单还停留在 v3.10 的"序列化锁 / mermaid / 缓存"三项。补回归用例让每个新决策都有对应保护,落地 + 后续重构能立刻发现回归 |
| **D-CI** | D-AY 标 ⚠️ "由 D-BQ / D-CF 收紧";描述里明确最终 in-flight 状态列表是 `pending/rendering_mermaid/pandoc/finalizing`(原文漏 finalizing);说明 invalidated 是显式终态不算 in-flight | D-AY 在表中位置较前(170 行),实施者通读时容易在最初接触到的"in-flight 状态列表"按字面实现,漏掉后面 D-BQ/D-CG 加的状态。一致性大于"决策表是历史日志"——表是当下的合约,不是 changelog |
| **D-CJ** | POST `/proposal.docx` 与 GET `/proposal.docx` **都先查 latest DocxJob 状态**:① POST cached 路径要求 `cached.exists() AND latest.status='done'` 才命中,latest=invalidated 走 INSERT 新建;② GET download 路径按 latest.status 分流——invalidated → 结构化 409 `{code: "docx_invalidated", docx_job_id, message}`;非 done → 409 `{code: "docx_not_ready"}`;done 但文件丢失 → 自动 repair 为 failed + 409 `{code: "docx_missing"}` | v3.15 D-CG 把 done 改 invalidated 但端点只看文件存在,unlink 失败或人为残留时 POST cached / GET download 仍返回旧 DOCX,违反 REQUIREMENTS FR-5.7 失效承诺。结构化 409 让前端按 code 分支展示("原文档已更新"vs"还没生成"vs"文件丢失"),用户行为指引也清晰 |
| **D-CK** | POST `/proposal.docx` cached 分支返回 latest done 的 `docx_job_id`,而非 `None`;前端拿到 id 后可继续 GET `/docx-job/{docx_job_id}` 轮询,markdown 重生成把它改成 invalidated 时**前端能感知**(原方案 cached 返 None → 前端没有轮询入口) | REQUIREMENTS FR-5.7 / D-CG 假设"前端 GET 看到 invalidated"。但 v3.15 cached 路径返回 `docx_job_id=None`,刷新页面后前端没有"最近 DOCX job"的查询入口,看不到 invalidated 切换。返回 latest id 后整个状态变迁链路对前端透明 |
| **D-CL** | 修正 v3.15 D-CH 测试:① `test_on_stage_rowcount_zero` 的初始 status 从 `failed` 改 `pending`,模拟 mermaid 跑完之后 cleanup 抢标(monkeypatch `_render_mermaid` 在返回前 UPDATE failed),才会真正走到 `on_stage("pandoc")` 的 _StaleJob 路径;② **真正抽 `_commit_done` helper**(原 v3.15 注释里写"假设抽出"但代码仍是内联),`generate_docx_task` 改成 `return await _commit_done(...)`;③ 补 `db` / `client` / `project_factory` / `docx_job_factory` fixture 说明,且每个用例的 import / state 都按可执行标准写 | v3.15 测试代码有三处不能跑:status=failed 让 worker 入口直接 return 走不到 _StaleJob;`_commit_done` 没真抽出来;`run_id=...` 占位符 + 缺 db fixture / 缺 import。可执行的测试才是回归保护,纸面用例反而误导 |
| **D-CM** | `assemble` 节点 UPDATE 范围**从 `status='done'` 扩到 `('done','pending','rendering_mermaid','pandoc','finalizing')` 全部失效**;`generate_docx_task` 入口 SELECT status 时 **`'invalidated' → 直接 return`**(同 done/failed 的快速退出路径)| v3.15 assemble 只 invalidate done,但 markdown 重生成时旧 DOCX 任务可能仍在 pending/rendering/pandoc/finalizing,task 后续完成会把"基于旧 markdown 的产物"写成 done(WHERE status='finalizing' 命中),POST cached 路径就返回脏数据。覆盖全 in-flight 让"作废"完整;worker 入口的 invalidated 守护是双层防护(后续阶段切换 WHERE status='pending' 等也会失败,但显式守护让 log/语义更清晰)。**短期不变量**:目前 spec 不支持 markdown 重生成,assemble 多次执行只在 LangGraph checkpoint 续跑同一 thread_id 时发生,这种情况上述机制已闭环;**长期**(若加 /restart 端点):应给 DocxJob 加 `source_run_id` 或 `proposal_hash`,worker 在每个阶段切换前校验仍是当前 proposal,作为更精细的失效边界 |
| **D-CN** | REQUIREMENTS DocxJob 表把状态分两类:① **对外业务状态**(API 暴露):`done` / `failed` / `invalidated` + 三个 in-flight 经映射成 `processing`;② **实现层不暴露**:`finalizing`(经映射 `processing`)、`updated_at` 字段。原措辞"invalidated 不暴露 API"与 D-CG"前端看到 invalidated"矛盾,明确划分两类后语义一致 | 措辞矛盾会让前端实施者无法判断要不要为 invalidated 设计 UI 文案。划清"业务状态 vs 实现细节"两类,让 GET `/docx-job` 的契约和 REQUIREMENTS 描述对得上 |
| **D-CO** | GET `/proposal.docx` 下载端在判 latest.status 之前先做 finalizing + file exists → done 的 inline repair(复用 D-CD 同款 SQL) | v3.16 D-CD 只在 GET `/docx-job` 端做 repair,但前端可能直接点"下载"绕开轮询;task 已 rename 但 done UPDATE 失败时 latest 卡 finalizing,download 按 D-CJ 分流会返回 docx_not_ready,实际文件已就位完全可下载。下载端也加 inline repair 让两条用户路径行为一致 |
| **D-CP** | D-CG 标 ⚠️ "由 D-CM 扩展",描述明确作废范围是 done + 全 in-flight;REQUIREMENTS FR-5.7 同步说明"已完成与正在生成中的 DocxJob 都会被作废" | v3.16 已把代码 UPDATE WHERE 扩到 in-flight,但 D-CG 决策行 + FR-5.7 文字仍写"done → invalidated"。文档落后于代码会让实施者照旧描述实现,把 in-flight 漏掉 |
| **D-CQ** ⚠️ 由 D-CV / D-CU 收紧 | `_commit_done` rowcount==0 时新增 invalidated 分支:**best-effort unlink final_path** 后返回 `{"status": "invalidated"}`;rename 之前再查一次 status,看到 invalidated 直接 unlink tmp + 退出 | v3.16 task 在 finalizing 抢占之后、rename → done 期间,assemble 可能已经把 status 改 invalidated。D-CM 入口守护拦不到这种已经过了 SELECT 的 task,rename 把"基于旧 markdown"的产物落到 final_path。**最终不变量(D-CV)**:DB 是 source of truth、文件残留是 best-effort 失败的回退,不再要求强 unlink 不变量;真正"finalizing repair 安全"靠 D-CU(新 job 进 rendering 时强制 unlink 旧 final_path)|
| **D-CR** | 测试代码补 `import asyncio`;`conftest.py` 补完整 fixture(`db` / `project_factory` / `docx_job_factory` / `auth_client`),`auth_client` 通过 `app.dependency_overrides[get_current_user]` 跳过鉴权 | v3.16 D-CL 测试用了 `asyncio.create_subprocess_exec` 但没 import asyncio,直接 NameError;`db` / `client` 等 fixture 假设存在但 conftest 示例只给了 docx_job_factory / project_factory;HTTP 用例没 auth 处理,GET 会先撞 401。补完整后才是真正可执行的回归用例 |
| **D-CS** | 补两个 D-CM 直接回归用例:① `test_assemble_invalidates_inflight_jobs` 参数化跑 4 个 in-flight 状态(pending / rendering_mermaid / pandoc / finalizing)→ 都被改成 invalidated;② `test_commit_done_skips_when_invalidated_and_unlinks_final` 验证 finalizing 被 assemble 抢标 invalidated 后 _commit_done 不写 done + unlink final_path | v3.16 测试集只有 `test_assemble_invalidates_existing_done_docx`,只覆盖了 done 路径;D-CM 把作废扩到 in-flight 但没回归。同样 D-CQ 的 _commit_done 新分支需要专门用例。两个用例直接覆盖核心不变量 |
| **D-CT** ⚠️ 由 D-CV 收紧 | R12 风险行 invariant 改成 `latest valid DocxJob.status='done' AND 文件存在 ⇔ 可下载`(承认 invalidated 期间文件可残留,API 不放行);缓解链路扩展到 D-CJ / D-CM / D-CO / D-CQ 全套 | v3.16 R12 写的是"DB done ⇔ proposal.docx 文件存在",但 D-CJ 已经把"可下载"决策从"看文件"换成"看 latest job"。**最终(D-CV)**:不变量分两层 — 业务层"DB 是 source of truth",物理层"finalizing 期间 file 存在 ⇔ 当前 task 已 rename"(D-CU 强制) |
| **D-CU** | `generate_docx_task` **切到 `rendering_mermaid` 后**立即 `final_path.unlink(missing_ok=True)`(顺序:先 UPDATE 状态再 unlink);`OSError` → UPDATE failed + raise 退出本任务 | v3.17 D-BY/D-CD/D-CO/D-CJ POST 四处 finalizing repair 都是"看文件存在 → UPDATE done"。但 D-CT/R12 已承认 invalidated 期间允许残留旧文件 — 旧 proposal.docx 残留 + 用户 POST 新 job 进 finalizing,这四处 repair 会把新 job 错标 done,实际下载的是**旧 markdown 的产物**,严重误导。修复方式:让物理不变量"finalizing 期间 final_path 存在 ⇔ 当前 task 已 rename"严格成立 — 切到 rendering 后立即 unlink 旧 final_path。**unlink 必须在状态切换之后**(而不是切换之前),才能与 D-BX 的状态前置链路一致 — 状态前置保证只有当前 task 走得到 rendering 阶段,unlink 才不会被旧 task 误触发。unlink 失败 → 标 failed 让用户重试,因为如果连旧文件都删不掉,后续 rename 也不可信 |
| **D-CV** | 不变量分两层明确:① **业务层**(API 契约)`API 可下载 ⇔ latest DocxJob.status='done' AND 文件存在`(DB 是 source of truth);② **物理层**(D-CU 维护)`finalizing 期间 final_path 存在 ⇔ 当前 task 已 rename`。其余时刻文件残留是 best-effort cleanup 失败的回退,**下次新 job rendering 会被 D-CU 强制清**;D-CQ 注释、R12 文字、D-CT 描述都按此口径同步 | v3.17 D-CQ 注释写"保证 DB invalidated ⇒ 文件不存在",但 R12 又写"invalidated 期间允许文件残留"— 同一份文档两个不变量自相矛盾,实施者会在两条路径上做出冲突的清理逻辑(强清 vs 不清)。明确两层不变量后语义一致:invalidated 期间允许残留(弱),但 finalizing 期间必须严格(强,由 D-CU 在 rendering 阶段提前清);unlink 失败的 best-effort 路径也有自愈兜底 |
| **D-CW** | conftest.py 新增 `user_factory`;`project_factory` 调用 `user_factory()` 创建真 User 后引用 `created_by=user.id`(原默认 `created_by=1` 在没有 admin 用户时会撞 FK constraint);`auth_client` 改用 `httpx.ASGITransport(app=app)` + `AsyncClient(transport=transport, ...)`(httpx≥0.28 把 `AsyncClient(app=...)` 弃用) | spec §5.1 已固定 `httpx>=0.28`,但 v3.17 测试 fixture 还用了 0.28 弃用的 `AsyncClient(app=app)`,装上就报错;`Project.created_by` 是非空 FK 但 fixture 没插 User 就引用 `created_by=1`,collection 阶段 FK 校验直接失败。补 user_factory + ASGITransport 才能让 D-CH/D-CL/D-CR/D-CS 用例真正可执行 |
| **D-CX** | 补两个 D-CU 直接回归用例:① `test_new_job_unlinks_stale_final_at_rendering` — 旧 `proposal.docx` 存在时,新 job 切到 rendering 后必须 unlink,且 mermaid 启动在 unlink 之后;② `test_unlink_oserror_marks_job_failed_and_skips_render` — unlink 抛 `OSError` → job 标 failed,mermaid/pandoc 都不被启动 | D-CU 是 v3.18 的核心新不变量,但 D-CH/D-CS 测试集只覆盖 assemble invalidation 与 _commit_done 分支,没用例直接断"rendering 阶段会清旧文件"。补两个用例后 D-CU 的正常 / 失败两条路径都有回归保护,后续重构能立刻发现回归 |
| **D-CY** | conftest.py 所有 `@pytest.fixture async def` 改成 `@pytest_asyncio.fixture`(与 §18.1 一致);文档说明 strict 模式下 `@pytest.fixture` 不识别 async 函数,要么用专门装饰器要么 pyproject 配 `asyncio_mode = "auto"` | v3.18 D-CW 加 fixture 时用了通用 `@pytest.fixture`,但 pytest-asyncio strict 模式下整个 fixture 不会被识别为 async,fixture 实际不生效会让所有 D-CR/D-CS/D-CX 用例报参数缺失。统一显式装饰器最稳 |
| **D-CZ** | R12 finalizing repair 文字从"三处"改成"四处"(加 POST cached,D-CJ POST);顶部摘要 + D-CU 决策行措辞从"rendering 前 unlink"改成"切到 rendering 后立即 unlink",顺序更明确 | v3.18 R12 写"finalizing 三处 repair(cleanup / GET /docx-job / GET 下载)",但代码里 POST `/proposal.docx` 也做 finalizing → done 修复,漏列让 R12 缓解链路与实际不一致;"rendering 前 unlink"字面会被实施者解读成"在状态切换之前",可能让 unlink 在 pending 状态下被多个 task 误触发,削弱 D-BX 状态前置守护 |
| **D-DA** | `test_new_job_unlinks_stale_final_at_rendering`:① proposal.md 写最小 mermaid block 触发 mmdc;② `create_subprocess_exec` 完全 fake(不调真实 mmdc/pandoc),fake 内部 touch png/docx 模拟成功;③ 直接断 `unlink_stale < mmdc < pandoc` 顺序,去掉 if 守护 | v3.19 测试有三个削弱断言强度的细节:proposal.md 是 `# t` 没 mermaid → mmdc 永远不会被触发;spy_subproc 后续仍 await 真实 `create_subprocess_exec` → 依赖宿主 mmdc/pandoc;关键顺序断言被 `if "mmdc" in call_log` 守护,本机没装 mmdc 时整个断言被跳过,等于无验证。修正后 D-CU 顺序保证有真正回归 |
| **D-DB** | OSError 测试用 `real_unlink = Path.unlink` 在 `monkeypatch.setattr` 之前保存原引用;`boom_unlink` 兜底分支调 `real_unlink` 而不是 `Path.unlink.__wrapped__` | `monkeypatch.setattr(Path, "unlink", boom_unlink)` 之后 `Path.unlink` 已是 `boom_unlink`,`__wrapped__` 不一定存在(monkeypatch 不会自动留 wrap 引用)。虽然测试逻辑里"不会跑到"那条分支,但写法本身脆,后续若加任何对其它路径的 unlink 都会触发 AttributeError;先保存原引用是最稳的兼容方式 |
| **D-DC** | D-CU 代码注释里 finalizing repair "三处" → "四处"(对齐 R12 / D-CZ);§18.5 标题改"汇总(D-CH / D-CL / D-CR / D-CS / D-CW / D-CX / D-CY / D-DA / D-DB)";测试文件头注释同步加 D-CX/D-DA/D-DB | 代码注释和决策表里"四处 repair"已对齐,但 D-CU 内联注释还停在"三处",实施者读代码段会按旧口径理解,可能漏 POST cached 路径;§18.5 标题落后于实际包含的决策项,标签同步只是文档一致性,但落地时是工程师对照决策表跑测试的入口 |
| **D-DD** | `project_factory` 加 `create_done_run: bool = True` 参数,默认创建一条 `Run(status='done', langgraph_thread_id=th_xxx, started_at=now, finished_at=now)`;conftest.py 顶部补 `from datetime import datetime, timezone` 与 `from bid_app.models import Run` | `generate_docx_task` 入口前置:`SELECT id FROM runs WHERE project_id=:p AND status='done'`,没 Run 就 RuntimeError("no completed run")。v3.20 fixture 只建 Project 不建 Run,所有直接调 task 的用例(test_on_stage_rowcount_zero / test_new_job_unlinks_stale / test_unlink_oserror_marks)在到达 unlink 之前就报错,D-CU 测试失效。让 fixture 默认产出"DOCX 可生成所必需的最小项目状态"是最贴近 P6 真实入口前置的写法 |
| **D-DE** | `test_unlink_oserror_marks_job_failed_and_skips_render` 在 docx_job_factory 之后加一行 `(Path(project.dir_path) / "proposal.md").write_text("# t\\n", encoding="utf-8")` | 即便补了 D-DD 的 done Run,§13.3 还有第二道前置:`if not md_path.exists(): raise RuntimeError("proposal.md missing")`。这条 raise 早于 D-CU 的 unlink 触发,导致 OSError 路径根本没被验证。补 proposal.md 让 task 能走到 D-CU 那一步真触发 OSError |
| **D-DF** | 新增 `test_unlink_happens_after_rendering_status_update`:① monkeypatch `bid_app.worker.tasks.session_factory` 用 `_SessionSpy` 包装,捕捉 `UPDATE ... rendering_mermaid` 那条 SQL;② monkeypatch `Path.unlink` 记录 stale 路径删除;③ 断 `call_log.index("update_rendering_mermaid") < call_log.index("unlink_stale")` | D-DA 已经断 `unlink_stale < mmdc < pandoc`,但还没直接验证 D-CU 的核心顺序保证 — UPDATE 状态切换必须在 unlink 之前。这个顺序是 D-CU + D-BX 链路的关键:状态前置(WHERE status='pending')保证只有当前 task 切到 rendering,切换之后再 unlink 才不会被 pending 阶段竞态的多个 task 误触发。直接 spy session.execute 锁死这一不变量,后续重构若调换两步顺序立刻被测试捕获 |
| **D-DG** | conftest.py 加 `mock_redis_lock` fixture:`monkeypatch.setattr("bid_app.services.docx_export._redis_lock", _no_redis_lock)`,no-op asynccontextmanager;四个直接调 `generate_docx_task` 的 D-CX/D-DA/D-DB/D-DF 用例都 use 这个 fixture | `export_docx` 串行化包装在 `_module_lock`(asyncio.Lock,同进程 OK)之后还有 `_redis_lock`(`redis.asyncio.from_url + r.lock(...)`,需要真实 Redis)。直接调 task 的测试只 fake 了 subprocess,没 fake Redis lock,在没真实 Redis 的 CI 环境会卡在 `r.ping()` / `lock.acquire()` 上,根本走不到 mermaid / on_stage / pandoc / unlink 等核心断言。单独抽 fixture 让 use 它的用例语义清晰(任何不需要真 Redis 的 task 单测都加上)|
| **D-DH** | `project_factory` 改成"单事务 add → flush → 可选 add(Run) → 单次 commit → refresh(p)";原"commit → refresh → add(Run) → commit"双 commit 路径在 SQLAlchemy 默认 `expire_on_commit=True` 下让 p 在第二次 commit 时进 expired 状态,返回后访问 `p.id` / `p.dir_path` 触发 async lazy-load 在已关闭 session 上失败 | session_factory 没显式配 `expire_on_commit=False` 时(spec 也没明确配),double commit 必出 detached/lazy-load 问题。flush 拿到 p.id 之后 add Run + 单次 commit,语义干净 + 无 expire 风险;refresh 在 commit 后单次拿全字段,后续返回的 p 在 detached 状态下也能直接读(因为已经 hydrated)|
| **D-DI** | §18.5 标题在 v3.20 加完 D-DA/D-DB 后,v3.21/v3.22 的 D-DD/D-DE/D-DF/D-DG/D-DH 都没补;统一改成"汇总(D-CH / D-CL / D-CR / D-CS / D-CW / D-CX / D-CY / D-DA / D-DB / D-DD / D-DE / D-DF / D-DG / D-DH)";test_docx.py 头注释同步 | 标题与节内容长期错位会让人误以为某些决策没回归用例覆盖。一次性把 v3.20-v3.22 加的标签都同步进标题,后续维护只需在新增决策时同步即可 |
| **D-DJ** ⚠️ 由 D-DM / D-DN / D-DO 收紧 | conftest.py 的 `db` / `user_factory` / `project_factory` / `docx_job_factory` / `auth_client` 都依赖 schema fixture(原 `db_engine`,**最终经 D-DN 改为依赖 `_use_test_session_factory`** 拿到的是已 monkeypatch 过 session_factory 的环境)| v3.22 之前完全不依赖 schema fixture,DOCX 测试不会触发 `Base.metadata.create_all`,撞 "relation does not exist" 或污染默认库。**最终实现** D-DM 把 URL 隔离到 `_test` 库 + 名字校验、D-DN 把 session_factory / get_db 真正切到 test engine、D-DO 修注释口径,D-DJ 的"避免污染开发库"目标才真正闭环 |
| **D-DK** | 两处 assemble 测试(`test_assemble_invalidates_existing_done_docx` / `test_assemble_invalidates_inflight_jobs`)的 `state["run_id"]` 从写死 `1` 改成从 DB `SELECT id FROM runs WHERE project_id=:p ORDER BY started_at DESC LIMIT 1` | `project_factory(create_done_run=True)` 默认建 Run,但 fixture 隔离 / 测试顺序变化时该 Run 的 id 不一定是 1(可能是 2、3、N)。写死会让"更新错 Run"或"不更新任何 Run"的 bug 静默通过。查真实 id 让测试与 fixture 行为耦合,后续 Run 模型改字段也不影响 |
| **D-DL** | `tests/integration/test_docx.py` 头注释补全 D-DD/D-DE/D-DH/D-DJ/D-DK 标签,与 §18.5 标题对齐(D-DI 只同步了标题,文件头还停在 v3.20 状态) | D-DI 的承诺包含"test_docx.py 头注释同步",但 v3.22 落地时只改了标题。文件头是工程师按 grep 标签找用例的入口,标题与注释口径一致才能让"决策 ↔ 用例"双向可追溯 |
| **D-DM** | `Settings` 加 `test_database_url` property:在 `database_url` 基础上把 `database` 名追加 `_test` 后缀;`db_engine` fixture 用它,且启动校验"DB 名必须以 `_test` 结尾",不满足直接 `RuntimeError` 中止 | v3.23 `db_engine` 直接用 `settings.database_url`,teardown 的 `drop_all` 在本地 `.env` 指向开发库时会**直接清空开发数据**。独立测试库 + 名字强校验是双层防线:即使有人误改 `test_database_url` property 跑一个不带 `_test` 的库,fixture 也会抢在 create_all 之前 raise。后续若团队需要指向另一台 DB 服务器,可加 `test_postgres_*` 字段覆盖 property |
| **D-DN** | conftest.py 加 `_use_test_session_factory` fixture:① `async_sessionmaker(db_engine, expire_on_commit=False)` 创建 TestSession;② 用 `monkeypatch.setattr` 替换 `bid_app.db.session_factory` 与 `bid_app.workflow.sync` / `bid_app.workflow.nodes.assemble` / `bid_app.worker.tasks` / `bid_app.services.concurrency` / `bid_app.services.llm` 等已 import 的引用;③ `app.dependency_overrides[get_db]` 让 HTTP 端点也走 test session;`db` / `user_factory` / `project_factory` / `docx_job_factory` / `auth_client` 都改成依赖 `_use_test_session_factory` 而不是裸 `db_engine` | v3.23 D-DJ 只让 fixture 依赖 `db_engine` 触发 schema create_all,但**应用代码**(worker tasks / assemble 节点 / API 端点)仍用模块顶部 import 的全局 `session_factory`,实际写入还是默认数据库。必须显式 monkeypatch 各模块已 import 的引用 + 替换 `get_db` 才能让"测试只打到 test engine"的承诺落地。`expire_on_commit=False` 同时缓解 D-DH 的 detached 风险扩散到 fixture 之外的代码路径 |
| **D-DO** | `db_engine` fixture docstring 去掉"session-scope 共享 schema"误导;明确"默认 function scope,每次测试独立 create/drop";如需 session-scope 加速要显式 `scope="session"` 并自行处理事务回滚 | v3.23 注释里写"session-scope 共享 schema, test-scope 包在事务外回滚也行"是错的 — `@pytest_asyncio.fixture` 默认 function scope。注释和实际行为不一致会让实施者误判测试生命周期(以为只 create_all 一次,实际每个测试都做);要么改注释,要么显式 scope。当前选 function 维持"测试隔离强"的默认 |
| **D-DP** | `tests/integration/test_docx.py` 顶层 **删除** `from bid_app.db import session_factory`;`test_unlink_happens_after_rendering_status_update` 用例签名加 `_use_test_session_factory`,内部 `real_make_session = _use_test_session_factory`(原 `from bid_app.db import session_factory as real_session_factory` 删) | 顶层 import 在 conftest fixture monkeypatch **之前**求值,把名字 `session_factory` 绑定到旧全局对象;后续 `monkeypatch.setattr("bid_app.db.session_factory", ...)` 改的是 `bid_app.db` 模块属性,不影响测试模块本地名字 — 等于绕过 D-DN 让该测试打到默认数据库。删顶层 import + 改用 fixture 注入是唯一干净方案 |
| **D-DQ** | `_use_test_session_factory` 的 `targets` 列表补 `bid_app.workflow.nodes.write_chapter`(_resolve_api_key 等用)和 `bid_app.api.health`(/health SELECT 1);`client` fixture 改依赖 `_use_test_session_factory` 而不是裸 `db_engine`;补维护提示:`rg "from .*db import session_factory"` 找漏的模块 | v3.24 D-DN 列了 6 个模块,但漏了 §11.2 (write_chapter) 与 §15.4 (health)。两者都 import 了 session_factory,实际写入会打到默认库。`client` fixture 依赖 `db_engine` 时 conftest 不会触发 `_use_test_session_factory` 的 setup → `app.dependency_overrides[get_db]` 没生效,e2e 测试经过 HTTP 路径打到默认库 |
| **D-DR** | `db_engine` 校验改用 `sqlalchemy.engine.make_url(url).database.endswith("_test")`,不再用 `"_test" in url.rsplit("/", 1)[-1]`;错误信息删 `TEST_DATABASE_URL` 环境变量提法,改成"修改 `settings.test_database_url` property 让数据库名以 `_test` 结尾" | 字符串切片 `rsplit("/", 1)[-1]` 在 URL 含 query string 或末尾斜杠时会撞坑;`make_url(...).database` 是 SQLAlchemy 解析后的纯库名,精准。错误信息提 `TEST_DATABASE_URL` 与 D-DM"不读环境变量"自相矛盾 — 改成指向 property 名一致 |
| **D-DS** | `docker/init-test-db.sh`:postgres 容器首启 `docker-entrypoint-initdb.d/` 自动 `CREATE DATABASE ${POSTGRES_DB}_test`;§17.5 加文档,本地非 docker 走 `./scripts/create-test-db.sh` 或手动 `psql -c CREATE DATABASE ...`;`Base.metadata.create_all` 只建表不建库,空 `_test` 库时 db_engine 直接 connect refused 是预期信号 | D-DM 拼出测试库 URL 但没说库怎么来。create_all 在不存在的 DB 上跑,只会拿到 connect refused,实施者会误以为是 fixture bug。在 docker init 时一次性建库最自然,本地非 docker 路径文档化,避免每次测试前忘记建库 |

### 3.2 v2 → v3 修正项一览

| # | 原 v2 缺陷 | v3 正确做法 | 章节 |
|---|---|---|---|
| 1 | API Key 运行时反查 ApiKey 表(伪快照) | Project 加 `encrypted_api_key_snapshot` BLOB,`/start` 拷贝快照,运行时只从该字段取 | §8 / §9 / §10.1 / §11.2 / §15.1 |
| 2 | parse_outline → 直冲 pick_chapter,无 P4 暂停 | 新增 `outline_review` interrupt 节点 + `/confirm-outline` 端点 | §10.2 / §10.7 / §15.1 |
| 3 | review/retry 都模糊地走 `graph.astream(None)` | 拆 `start_workflow_task` / `resume_review_task` / `retry_failed_chapter_task` 三个 arq 任务 | §10.5 |
| 4 | retry 仅把 status='failed' → 'pending',retry_count 不重置 | retry 事务:status='pending'、retry_count=0、last_error=NULL、本轮 ChapterVersion 标 abandoned、记 ReviewEvent | §10.5 / §15.2 |
| 5 | `new_retry >= max_retry` off-by-one | 改 `>`;字段语义补"配置 N 表示允许 N 次重写,第 N+1 次自动 skip" | §10.4 / §11 |
| 6 | docx 输出 `{name}_{date}.docx` 但下载查 `proposal.docx` | 缓存固定 `proposal.docx`,FileResponse(filename=动态名) | §13.1 / §15.3 |
| 7 | 先 enqueue 再 INSERT DocxJob,有竞态 | (v3 方案)先 INSERT flush 拿 id → enqueue → UPDATE arq_job_id;**v3.5 进一步收紧为先 commit → enqueue → UPDATE,见 D-AK** | §15.3 |
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
    def test_database_url(self) -> str:
        """⭐ D-DM:独立测试库 URL,与生产/开发库严格分离。
        策略:复用 postgres_user/password/host/port,**database 名固定加 `_test` 后缀**;
        测试 fixture(`db_engine`)启动时再校验名字必须含 `_test`,避免误删开发库。
        本 property 不读环境变量,所以 conftest 直接 `settings.test_database_url`
        就能拿到一致的派生 URL,无需另设 `TEST_DATABASE_URL` 环境变量;
        如果团队需要指向另一台 DB 服务器,可在 Settings 加 `test_postgres_*` 字段
        覆盖此 property。"""
        pwd = quote(self.postgres_password, safe="")
        return (f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}_test")

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
    # pending|generating|awaiting_review|reviewing|approved|skipped|failed|retrying
    # ⭐ D-AI 中间态:
    # - reviewing: API /review 行锁内切的;worker 接管后 → generating(revise) /
    #              approved / skipped(由 update_state 节点)
    # - retrying:  API /retry  行锁内切的;worker 接管后 → pending(重置)→ generating
    retry_count: Mapped[int] = mapped_column(default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ⭐ D-AR + D-BF:API 切到 reviewing/retrying 时写、write_chapter 节点切
    # generating 时也写;cron `cleanup_stale_chapters` 按状态分段超时回滚:
    # reviewing/retrying 60s → awaiting_review / failed
    # generating          15min → failed(覆盖 worker SIGKILL/OOM 没进 SlotLost 的窗口)
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
from sqlalchemy import String, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

class ReviewEvent(Base, TimestampMixin):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(16))  # approve|revise|skip|retry_failed
    feedback_text: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    # ⭐ D-BI:SlotLost 把 approve/skip 章节回滚到 awaiting_review 时,本事件
    # **没真正生效**,标 aborted=true 避免前端"上次审核人/决策"误显示已撤销动作。
    # 默认 false,正常审核流程不动这个字段;查询"最近一次有效审核"加 WHERE NOT aborted
    aborted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    #   ('pending','rendering_mermaid','pandoc','finalizing')  ⭐ D-S + D-BQ
    #   —— 同项目同时只允许 1 个 in-flight 的 DOCX 任务;finalizing 也是 in-flight,
    #   必须在唯一约束里(否则 finalizing 期间另一个 job 能新建,产生竞态)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    arq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ⭐ D-S:nullable=True;入队前先 INSERT 占位拿主键 id,enqueue 后再 UPDATE arq_job_id。
    # 唯一性走 partial unique index,允许多条 NULL。
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|rendering_mermaid|pandoc|finalizing|done|failed|invalidated
    # ⭐ D-BQ:finalizing = "tmp 文件已生成,正在 atomic rename 成 proposal.docx"。
    # 这个短窗口让 cleanup/cron 可以扫到"DB done 但 rename 没完成"的中间状态;
    # done 的语义收紧为"rename 已成功 + 文件可下载",防 v3.11 的"先 done 再 rename"
    # 期间崩溃导致 download 端 409
    # ⭐ D-CG:invalidated = "上游 markdown 重新生成,本 DOCX 产物已过期"。
    # 由 assemble 节点(workflow 重写 proposal.md)同步标记;前端看到该状态
    # 应当展示"原文档已更新,请重新生成 DOCX"并提供新 POST 入口。
    # cleanup 不动 invalidated(它是显式终态,不是卡住);partial unique 也不算
    # in-flight,允许同 project 在 invalidated 上再 INSERT 新 pending
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ⭐ D-BH:每次 status 切换 SET updated_at=NOW();cron `cleanup_stale_docx_jobs`
    # 用 updated_at 而不是 created_at 判超时,避免误杀"等串行锁/真在跑 pandoc"的 job
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_func.now(), onupdate=sa_func.now(), nullable=False,
    )
```

> 注:`DocxJob` 模块顶部除已有 `from datetime import datetime`,还需 `from sqlalchemy import func as sa_func`(`onupdate=sa_func.now()` 让 SQLAlchemy 在 ORM 写入时自动填,但**纯 SQL `UPDATE` 不会触发**,故 task 显式 SET `updated_at=NOW()` 见下面 cleanup 与 task 实现)。

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
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),  # D-AR
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "index"),
    )
    op.create_index(
        "ix_chapters_processing", "chapters",
        ["status", "processing_started_at"],
        # ⭐ D-BZ:精准 partial WHERE — pending 仅在 retry worker 写过 processing_started_at
        # 才进索引,初始 pending(NULL)不进。reviewing/retrying/generating 必有时间戳。
        # 这才与"cleanup 扫的所有 chapter 都进索引,无关 chapter 不进"对齐
        postgresql_where=sa.text(
            "status IN ('reviewing','retrying','generating') "
            "OR (status='pending' AND processing_started_at IS NOT NULL)"
        ),
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
        # ⭐ D-BI:SlotLost 撤销 approve/skip 时标 true,前端"最近审核"过滤
        sa.Column("aborted", sa.Boolean, nullable=False, server_default=sa.text("false")),
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
        # ⭐ D-BH:每次 status 切换 task 显式 SET;cleanup 基于这个判超时
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_docx_jobs_arq_job_id", "docx_jobs", ["arq_job_id"],
        unique=True,
        postgresql_where=sa.text("arq_job_id IS NOT NULL"),
    )
    op.create_index(
        "uq_docx_jobs_project_inflight", "docx_jobs", ["project_id"],
        unique=True,
        # ⭐ D-BQ:finalizing 也是 in-flight,partial unique 必须覆盖,否则
        # 一个 job 在 finalizing 期间另一个 job 能新建 → 同 project 两个 in-flight
        postgresql_where=sa.text(
            "status IN ('pending','rendering_mermaid','pandoc','finalizing')"
        ),
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
    # ⭐ D-BK:每个切 generating 的位置都同步写 processing_started_at,
    # 让 cleanup_stale_chapters 的 generating 分支能扫到这个章节
    from datetime import datetime, timezone
    await sync_chapter_to_db(state["run_id"], current,
                             status="generating", retry_count=new_retry,
                             processing_started_at=datetime.now(timezone.utc))
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
import structlog
from datetime import datetime, timezone
from pathlib import Path

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status
from ...db import session_factory
from ...workflow.prompts.assemble import build_proposal

log = structlog.get_logger()


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

    # ⭐ D-CG + D-CM:重写 proposal.md 后 DOCX 缓存必须失效
    # 任何已生成的 proposal.docx 与对应 DocxJob 都不再代表当前 markdown:
    # - status='done':直接作废
    # - in-flight(pending/rendering_mermaid/pandoc/finalizing):同样作废
    #   D-CM:不只覆盖 done 是因为重生成 markdown 的同时旧 DOCX 任务可能仍在跑;
    #   不作废它们的话,task 后续 finalize 时会把"基于旧 markdown 的产物"标 done,
    #   POST cached 路径就会返回旧 docx。worker 入口需配合 invalidated 守护(见 §13.3)
    docx_path = project_dir / "proposal.docx"
    if docx_path.exists():
        try:
            docx_path.unlink()
        except Exception:
            log.exception("docx_invalidate_unlink_failed",
                          project_id=state["project_id"], path=str(docx_path))
    async with session_factory() as s:
        await s.execute(sa.text(
            "UPDATE docx_jobs SET status='invalidated', "
            "output_path=NULL, updated_at=NOW(), "
            "error=COALESCE(error,'') || "
            "  CASE WHEN COALESCE(error,'')='' THEN '' ELSE ' | ' END || "
            "  'markdown invalidated by new assemble' "
            "WHERE project_id=:p AND status IN "
            "('done','pending','rendering_mermaid','pandoc','finalizing')"
        ), {"p": state["project_id"]})
        await s.commit()

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
from pathlib import Path
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


class AcquireResult:
    """try_acquire_project_slot 的三态返回。"""
    def __init__(self, token: str | None, reason: str):
        self.token = token
        self.reason = reason   # "ok" | "full" | "already_active"

    @property
    def acquired(self) -> bool:
        return self.token is not None


async def try_acquire_project_slot(project_id: int) -> AcquireResult:
    """⭐ D-AB / D-AF / D-AN / D-AQ 严格版:四种返回(token / full / already_active / 自动 evict 后递归)。

    Lua 行为(D-AQ 修正版):
    1. project_id 在 SET + ALIVE 存在 → "already_active"(真在跑,拒绝)
    2. project_id 在 SET + ALIVE 不存在 → "stale" 返回 -2,Python 端做 evict
       (SREM + DB 同步标 failed)然后递归调用 try_acquire(D-AN 仍保持
       "SREM + DB 同步绑定",绑定位置在 Python 端)
    3. 否则数 alive 成员个数;< max → SADD + SET ALIVE_KEY → "ok"
    4. ≥ max → "full"
    """
    return await _try_acquire_inner(project_id, _allow_evict=True)


async def _try_acquire_inner(project_id: int, *, _allow_evict: bool) -> AcquireResult:
    import uuid
    token = uuid.uuid4().hex

    # Lua 返回:
    #  0 = full
    # -1 = already_active (in SET + alive exists)
    # -2 = stale (in SET + alive missing,需 Python 端 evict 后递归)
    #  1 = acquired
    script = """
        local exists = redis.call('SISMEMBER', KEYS[1], ARGV[2])
        if exists == 1 then
            -- 区分 already_active vs stale
            local alive = redis.call('EXISTS', KEYS[2])
            if alive == 1 then
                return -1
            end
            return -2
        end

        local members = redis.call('SMEMBERS', KEYS[1])
        local alive_count = 0
        for i, m in ipairs(members) do
            if redis.call('EXISTS', 'bid_app:project_alive:' .. m) == 1 then
                alive_count = alive_count + 1
            end
        end

        local max = tonumber(ARGV[1])
        if alive_count < max then
            redis.call('SADD', KEYS[1], ARGV[2])
            redis.call('SET', KEYS[2], ARGV[3], 'EX', tonumber(ARGV[4]))
            return 1
        end
        return 0
    """
    r = _r()
    try:
        ok = await r.eval(
            script, 2, ACTIVE_SET, ALIVE_KEY.format(project_id),
            settings.max_concurrent_projects, project_id, token, RESERVE_TTL,
        )
    finally:
        await r.aclose()

    if ok == 1:
        return AcquireResult(token, "ok")
    if ok == -1:
        return AcquireResult(None, "already_active")
    if ok == -2:
        if _allow_evict:
            log.warning("acquire_detected_stale", project_id=project_id)
            await _evict_stale_project(project_id)
            # 递归一次(_allow_evict=False 防无限循环)
            inner = await _try_acquire_inner(project_id, _allow_evict=False)
            if inner.reason == "ok":
                inner.reason = "ok"  # 保持
            return inner
        # 已经 evict 过仍 stale,极罕见 → 报错
        log.error("acquire_stale_after_evict", project_id=project_id)
        return AcquireResult(None, "stale_evicted")
    return AcquireResult(None, "full")


async def _evict_stale_project(project_id: int) -> None:
    """同步处理 stale 成员:SREM + DB 标 failed(D-AN 绑定原则:SREM 必须伴随 DB 同步)。"""
    r = _r()
    try:
        await r.srem(ACTIVE_SET, project_id)
    finally:
        await r.aclose()
    # DB 同步:与 cron reconcile_periodic 用同样口径
    async with session_factory() as s:
        await s.execute(
            sa.text("UPDATE projects SET status='failed' WHERE id=:p "
                    "AND status IN ('running','extracting','outlining')"),
            {"p": project_id},
        )
        await s.commit()
    log.warning("evicted_stale_project", project_id=project_id)


async def ensure_project_slot(project_id: int, token: str) -> bool:
    """task 入口调用:校验自己的 token 仍是 ALIVE_KEY 的值。
    True = slot 仍归我,可继续;False = 已被回收,task 应当退出或重新 acquire。"""
    r = _r()
    try:
        current = await r.get(ALIVE_KEY.format(project_id))
        return current == token
    finally:
        await r.aclose()


async def heartbeat_project(project_id: int, token: str) -> bool:
    """task 跑起来后续租到较短的 ALIVE_TTL(60s)。
    用 Lua CAS:仅当 ALIVE_KEY 的值仍是自己的 token 时才续租。
    返回:True=续租成功;False=token 已失效(reconcile 清过 / 别人重 acquire)。
    调用方在 False 时应当让 task 主动中止(D-AG)。"""
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
        ok = await r.eval(script, 1, ALIVE_KEY.format(project_id), token, ALIVE_TTL)
        return ok == 1
    finally:
        await r.aclose()


async def reconcile_periodic(ctx) -> None:
    """arq cron 每分钟跑一次 reconcile,catch worker 不重启但 heartbeat 异常的情况(D-AG)。"""
    zombies = await reconcile_active_projects()
    if zombies:
        # 把僵尸项目标 failed,运维可手动 retry
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET status='failed' "
                        "WHERE id = ANY(:ids) AND status IN ('running','extracting','outlining')"),
                {"ids": zombies},
            )
            await s.commit()


# ⭐ D-AR / D-AS / D-BF 中间态超时清理
STALE_CHAPTER_TIMEOUT_SECONDS = 60       # reviewing/retrying 超 60s 没被 worker 接管 → 回滚
# ⭐ D-BF:generating 卡住的兜底,worker 被 SIGKILL/OOM 时 SlotLost 分支跑不到,
# 这个超时要 > LLM 单章总时长(FR-3.10 = 600s)+ 心跳/调度余量
STALE_GENERATING_TIMEOUT_SECONDS = 60 * 15   # 15 分钟 = 600s + 5 min margin
STALE_DOCX_TIMEOUT_SECONDS = 30 * 60     # DocxJob pending 超 30 分钟 → failed


async def cleanup_stale_chapters(ctx) -> None:
    """arq cron 每分钟跑:回滚 API commit 后进程崩溃导致卡 reviewing/retrying 的章节(D-AR)。

    reviewing → awaiting_review(用户可重新提交审核)
    retrying  → failed(用户可重新点 retry)

    ⚠️ D-AV 修正:**先取真 alive ids 排除有 alive reservation 的 project**。
    原因:API try_acquire 设的 RESERVE_TTL=300s,远大于 cleanup 的 60s 阈值。
    若 worker 启动延迟(arq 排队 / 重启)在 60s~300s 之间,章节会被本 cron 误回滚,
    但 Redis 里 slot 仍被持有 → cron 与 reservation 冲突。
    修复:用 `get_alive_project_ids()`(SET ∩ ALIVE_KEY 实存)排除,**比直接排除
    SET 成员更精准**(D-BB:防 stale 自愈窗口期间的成员被错算成 alive)。

    ⚠️ D-BB 修正:SQL 用 `CAST(:active_ids AS int[])` 显式 typed array,
    避免 SQLAlchemy/asyncpg 对 Python list → Postgres array 类型推断踩坑。
    """
    # ⭐ D-BB:取真 alive(SET ∩ ALIVE_KEY)而不是整个 SET
    active_ids = await get_alive_project_ids()

    async with session_factory() as s:
        # 显式 typed array,空列表也安全(<> ALL(ARRAY[]::int[]) 恒 true)
        # ⭐ D-BF:三段超时分支(reviewing/retrying 60s,generating 900s),
        # 用 CASE 在 SQL 内同时处理,一次 cron 跑完
        # ⭐ D-BL:RETURNING 把 run_id 也带出来,后面切对应 project 状态
        result = await s.execute(sa.text(
            f"""
            WITH stale AS (
                SELECT c.id, c.status, c.run_id, r.project_id FROM chapters c
                JOIN runs r ON r.id = c.run_id
                WHERE r.project_id <> ALL(CAST(:active_ids AS int[]))
                  AND (
                    (c.status IN ('reviewing','retrying')
                     AND c.processing_started_at IS NOT NULL
                     AND c.processing_started_at <
                         NOW() - INTERVAL '{STALE_CHAPTER_TIMEOUT_SECONDS} seconds')
                    OR
                    -- ⭐ D-BS:retry worker 切完 pending 但 graph 没启动就崩 →
                    -- 章节卡 pending 且 processing_started_at NOT NULL;NULL 是
                    -- 初始 pending(从未跑过),不该回滚
                    (c.status = 'pending'
                     AND c.processing_started_at IS NOT NULL
                     AND c.processing_started_at <
                         NOW() - INTERVAL '{STALE_CHAPTER_TIMEOUT_SECONDS} seconds')
                    OR
                    (c.status = 'generating'
                     AND c.processing_started_at IS NOT NULL
                     AND c.processing_started_at <
                         NOW() - INTERVAL '{STALE_GENERATING_TIMEOUT_SECONDS} seconds')
                  )
            )
            UPDATE chapters c SET
                status = CASE
                    WHEN s.status='reviewing'  THEN 'awaiting_review'
                    WHEN s.status='retrying'   THEN 'failed'
                    WHEN s.status='pending'    THEN 'failed'
                    WHEN s.status='generating' THEN 'failed'
                END,
                processing_started_at = NULL,
                last_error = COALESCE(c.last_error, '') ||
                    ' [auto-rollback from ' || s.status || ' at ' || NOW()::text || ']'
            FROM stale s
            WHERE c.id = s.id
            RETURNING c.id, s.status AS old_status, c.status AS new_status,
                      s.project_id AS project_id
            """
        ), {"active_ids": active_ids})
        rows = result.fetchall()

        # ⭐ D-BL + D-BS:对回滚到 failed 的章节(generating / pending),把 Project
        # 切 awaiting_review;让用户能从 P5 看到 failed 章节并 /retry,而不是停在
        # reconcile 标的项目级 failed。仅当 Project 仍处于"工作流在跑"或"已被
        # reconcile 标 failed"的状态才切,不动 done / aborted / awaiting_review / queued
        gen_project_ids = sorted({
            r.project_id for r in rows
            if r.old_status in ('generating', 'pending')
        })
        if gen_project_ids:
            await s.execute(sa.text(
                "UPDATE projects SET status='awaiting_review' "
                "WHERE id = ANY(:ids) "
                "AND status IN ('running','extracting','outlining','failed')"
            ), {"ids": gen_project_ids})
        await s.commit()
    if rows:
        log.warning("cleanup_stale_chapters",
                    count=len(rows), skipped_active=len(active_ids),
                    rolled_back=[(r.id, r.old_status, r.new_status) for r in rows],
                    project_state_restored=gen_project_ids if rows else [])


async def cleanup_stale_docx_jobs(ctx) -> None:
    """arq cron 每 5 分钟跑(D-AS / D-AY / D-BH / D-BQ / D-BY):
    **所有 in-flight 状态**超时都标 failed;**`finalizing` 加文件存在 repair**。

    ⚠️ D-BH 修正:用 `updated_at` 判超时而不是 `created_at`。
    ⚠️ D-BQ 扩展:新增 `finalizing` 状态进 in-flight 列表。

    ⚠️ D-BY 修正:`finalizing` 单独处理 — task 已经 atomic rename 完成 tmp 但 DB
    `done` UPDATE 异常时,文件已存在但 DB 仍 finalizing。这种情况应当 **repair**
    成 done 而不是 failed,因为产物完整。规则:
    - finalizing + proposal.docx 存在 → repair 为 done
    - finalizing + 文件不存在 + updated_at 超时 → 标 failed(rename 之前崩了)
    其它 in-flight 状态超时直接 failed。
    """
    repair_done_count = 0
    async with session_factory() as s:
        # ⭐ D-BY repair pass 1:finalizing 但文件已存在 → done
        # 用 UPDATE...RETURNING + 在 Python 端校验文件存在,避免 SQL 直接读文件系统
        finalizing = (await s.execute(sa.text(
            "SELECT dj.id, dj.project_id, p.dir_path "
            "FROM docx_jobs dj JOIN projects p ON p.id = dj.project_id "
            "WHERE dj.status='finalizing'"
        ))).mappings().all()
        for row in finalizing:
            file_path = Path(row["dir_path"]) / "proposal.docx"
            if file_path.exists():
                # 文件已就位,task 在 done UPDATE 之前崩 → repair 成 done
                upd = await s.execute(sa.text(
                    "UPDATE docx_jobs SET status='done', "
                    "output_path=:p, finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='finalizing' RETURNING id"
                ), {"i": row["id"], "p": str(file_path)})
                if upd.first() is not None:
                    repair_done_count += 1
                    log.info("docx_finalizing_repaired_to_done",
                             docx_job_id=row["id"], project_id=row["project_id"])
        if repair_done_count:
            await s.commit()

        # pass 2:剩下的 in-flight(包括 rename 前崩的 finalizing)按超时标 failed
        result = await s.execute(sa.text(
            f"""
            UPDATE docx_jobs
            SET status='failed',
                error='auto-rollback: ' || status || ' > {STALE_DOCX_TIMEOUT_SECONDS}s',
                finished_at=NOW(),
                updated_at=NOW()
            WHERE status IN ('pending','rendering_mermaid','pandoc','finalizing')
              AND updated_at < NOW() - INTERVAL '{STALE_DOCX_TIMEOUT_SECONDS} seconds'
            RETURNING id, project_id, status
            """
        ))
        rows = result.fetchall()
        await s.commit()
    if rows or repair_done_count:
        log.warning("cleanup_stale_docx_jobs",
                    failed=len(rows), repaired_to_done=repair_done_count,
                    jobs=[(r.id, r.project_id, r.status) for r in rows])


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


async def get_alive_project_ids() -> list[int]:
    """⭐ D-BB:返回当前真"alive"(SET 成员 + ALIVE_KEY 仍存在)的 project ids。
    cleanup_stale_chapters 用它精准排除"有 worker 接管中"的项目,而不是排除整个
    ACTIVE_SET(后者可能含 stale 成员,虽然 D-AQ 会自愈但仍有时间窗口)。"""
    r = _r()
    try:
        members = await r.smembers(ACTIVE_SET)
        if not members:
            return []
        async with r.pipeline(transaction=False) as p:
            for pid in members:
                p.exists(ALIVE_KEY.format(pid))
            results = await p.execute()
        return [int(pid) for pid, alive in zip(members, results) if alive]
    finally:
        await r.aclose()


async def wake_queued_projects(arq_pool) -> int:
    """幂等地把 status='queued' 的项目按 FIFO 入队。SETNX 排他锁防并发。
    返回本次实际唤醒数量(供日志/监控用)。

    ⚠️ D-AP 修正:**不再用 SCARD 判断容量**。SCARD 把僵尸成员算进去,与 D-AN
    "alive count 才是真容量"冲突——会导致明明有可用名额(僵尸不算),却判满。
    改成直接调 try_acquire_project_slot,Lua 内部已经按 alive count 判断;
    返回 full → 退出循环。

    ⚠️ D-AX 修正:**异常 queued 项目立即标 failed,不再 continue**。
    原代码 already_active / stale_evicted / run missing 时只 continue,
    但 SKIP LOCKED 是事务级的,事务 commit 后行锁释放,**下一轮循环又会
    取到同一个 project**(状态没变),wake 卡死。改成立即 UPDATE status='failed'
    并写 last_error,这样下一次 SELECT 不再返回该 project,可以继续找下一个 queued。
    """
    woke_count = 0
    r = _r()
    try:
        got = await r.set(WAKE_LOCK, "1", nx=True, ex=30)
        if not got:
            return 0
        try:
            async with session_factory() as s:
                while True:
                    # 取一个 queued 项目(FIFO + 行锁,跨进程安全)
                    async with s.begin():
                        row = await s.execute(sa.text(
                            "SELECT id FROM projects WHERE status='queued' "
                            "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                        ))
                        next_pid = row.scalar_one_or_none()
                        if next_pid is None:
                            return woke_count
                        # ⭐ 直接 try_acquire,内部用 alive count 判断真实容量
                        result = await try_acquire_project_slot(next_pid)
                        if not result.acquired:
                            if result.reason == "full":
                                # 全局确实满了,本次 wake 退出
                                return woke_count
                            # ⭐ D-AX:already_active / stale_evicted 这类异常 queued
                            # 项目立即标 failed,防 SKIP LOCKED 死循环
                            log.error("wake_acquire_anomaly_marking_failed",
                                      project_id=next_pid, reason=result.reason)
                            await s.execute(sa.text(
                                "UPDATE projects SET status='failed' WHERE id=:p"
                            ), {"p": next_pid})
                            continue
                        slot_token = result.token
                        run_row = await s.execute(sa.text(
                            "SELECT id, langgraph_thread_id FROM runs "
                            "WHERE project_id=:p ORDER BY started_at DESC LIMIT 1"
                        ), {"p": next_pid})
                        run = run_row.one_or_none()
                        if run is None:
                            # ⭐ D-AX:run 缺失也是异常 queued,标 failed 让循环继续
                            log.error("wake_run_missing_marking_failed",
                                      project_id=next_pid)
                            await release_project_slot(next_pid, slot_token)
                            await s.execute(sa.text(
                                "UPDATE projects SET status='failed' WHERE id=:p"
                            ), {"p": next_pid})
                            continue
                        run_id, thread_id = run
                        # ⭐ 与 /start 路径口径一致(D-AL):新启动一律先入 extracting,
                        # 由 worker 跑 extract_documents 节点后自然进 outlining/running
                        await s.execute(sa.text(
                            "UPDATE projects SET status='extracting' WHERE id=:p"
                        ), {"p": next_pid})
                    # commit 后再 enqueue;若 enqueue 抛异常,补偿:释放 slot + 把项目改回 queued
                    try:
                        await arq_pool.enqueue_job(
                            "start_workflow_task",
                            project_id=next_pid, run_id=run_id, thread_id=thread_id,
                            slot_token=slot_token,
                        )
                        woke_count += 1
                    except Exception:
                        log.exception("wake_enqueue_failed", project_id=next_pid)
                        await release_project_slot(next_pid, slot_token)
                        async with session_factory() as s2:
                            await s2.execute(sa.text(
                                "UPDATE projects SET status='queued' WHERE id=:p"
                            ), {"p": next_pid})
                            await s2.commit()
                        return woke_count   # 让外层重试(下次 release 触发)
                    # 循环看下一个,直到名额满或队列空
        finally:
            await r.delete(WAKE_LOCK)
    finally:
        await r.aclose()


class SlotLost(Exception):
    """heartbeat 续租失败 = token 已被回收。task 应当中止(D-AG)。"""


@contextlib.asynccontextmanager
async def project_heartbeat(project_id: int, token: str):
    """task 运行时上下文,每 HEARTBEAT_INTERVAL 秒续租 alive TTL(D-AM)。
    续租失败(token 不再匹配)→ 设 lost_event,主循环用 ensure_project_slot 检测后 raise SlotLost。
    """
    lost_event = asyncio.Event()

    async def _loop():
        while not lost_event.is_set():
            try:
                ok = await heartbeat_project(project_id, token)
                if not ok:
                    log.warning("slot_lost_during_heartbeat",
                                project_id=project_id, token_prefix=token[:8])
                    lost_event.set()
                    break
            except Exception:
                log.exception("heartbeat_failed", project_id=project_id)
            try:
                await asyncio.wait_for(lost_event.wait(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                pass

    hb_task = asyncio.create_task(_loop())
    try:
        yield lost_event   # 主循环可以 if lost_event.is_set(): break
    finally:
        if not lost_event.is_set():
            lost_event.set()  # 触发 _loop 退出
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await hb_task
```

> ⚠️ **graph.astream() 不会被 cancel_event 中断**(asyncio 不能"打断"协程主循环外的协程)。worker task 主循环必须**显式**在每次迭代之间调用 `await ensure_project_slot(token)` 校验,token 失效 raise SlotLost 退出。见 worker tasks 实现:
>
> ```python
> async with project_heartbeat(project_id, token) as lost_event:
>     async for _ in graph.astream(...):
>         if lost_event.is_set() or not await ensure_project_slot(project_id, token):
>             raise SlotLost(f"slot lost during graph stream, project_id={project_id}")
> ```

**worker/lifecycle.py** 启动时调 reconcile + wake:

```python
async def on_startup(ctx):
    saver = AsyncPostgresSaver.from_conn_string(settings.langgraph_dsn)
    await saver.setup()
    ctx["checkpointer"] = saver
    ctx["arq_pool"] = ctx["redis"]   # arq 已经把 redis 连接放在 ctx['redis']
    zombies = await reconcile_active_projects()
    if zombies:
        # 把僵尸项目标 failed,运维可手动 retry(口径与 cron reconcile_periodic 一致)
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET status='failed' "
                        "WHERE id = ANY(:ids) "
                        "AND status IN ('running','extracting','outlining')"),
                {"ids": zombies},
            )
            await s.commit()
    await wake_queued_projects(ctx["arq_pool"])
```

**worker/tasks.py 的三类任务**(D-T 修正版,人工等待不占名额):

> ⚠️ **设计原则**:slot 代表"当前正在跑 LLM/工作流的项目"。worker 进程结束(因为 `interrupt` 退出 `astream` 循环)heartbeat 必停,alive TTL 过期会被 reconcile 判僵尸——所以 v3-pass1 的"awaiting_review 持续占名额"在物理上也不可能成立。**正确语义**:每个 task 进入时持有名额,任务返回(包括因 interrupt 自然结束)就释放。下一次 resume/retry 由 API 端点先 `try_acquire`,占到才入队;占不到返回 503。

```python
from arq.worker import func

from ..services.llm import ChapterGenerationFailed   # ⭐ D-AU


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
        async with project_heartbeat(project_id, token) as lost_event:
            await _set_project_status(project_id, "running")
            initial = await build_initial_state(project_id, run_id)
            async for _ in graph.astream(initial, config, stream_mode="values"):
                # ⭐ D-AM:每次 step 后校验 slot 仍持有,失效 raise SlotLost 退出
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during start, project_id={project_id}")
    except SlotLost:
        # ⭐ D-AW:start 阶段 SlotLost — 把 run 下所有 generating 章节标 failed,
        # project 切 awaiting_review(让用户能从 P5 retry,而不是 silent failed);
        # 没 generating 章节(还在 extract/outline 阶段)说明工作流刚起步,
        # 这种情况让 reconcile 标 failed(已有 cron 兜底)。
        await _slot_lost_compensation(project_id, run_id, current_chapter_id=None,
                                      action="start", decision=None)
        log.warning("start_workflow_task_aborted_slot_lost", project_id=project_id)
    except ChapterGenerationFailed as e:
        # ⭐ D-AU:章节级失败,不是 task 崩溃 — chapter 已被 write_chapter 节点
        # 标 failed + last_error,这里只切 project 状态;不写 errors.log;
        # **不 raise**(避免 arq 显示 task failed,用户从 P5 看到的应该是
        # "工作流暂停在 ch_N,可重试")。
        await _set_project_status(project_id, "awaiting_review")
        log.info("start_workflow_task_chapter_failed",
                 project_id=project_id, chapter_index=e.chapter_index)
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "start_workflow_task crashed",
                               run_id=run_id, thread_id=thread_id, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        # ⭐ D-BA:Run.status 与 Project.status 同步标 failed
        await _fail_project_and_run(project_id, run_id, "start_workflow_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
async def resume_review_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                             resume_payload: dict, slot_token: str,
                             reviewer_id: int | None = None,
                             chapter_id: int | None = None):
    """从 interrupt 恢复。worker 入口写 ReviewEvent(D-AC),保证事件与执行同生同灭。
    ⚠️ D-AH:token 拿到后立即进 try/finally,所有 DB 操作放 try 内。任何异常都释放 slot。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "resume")
    if token is None:
        return

    review_decision: str | None = None
    review_event_id: int | None = None
    try:
        # ⭐ D-AC + D-AZ:worker 入口写 ReviewEvent + 按 decision 切章节状态
        if reviewer_id is not None and chapter_id is not None:
            kind = (resume_payload or {}).get("kind")
            if kind == "chapter_review":
                review_decision = resume_payload.get("decision")
                async with session_factory() as s:
                    rev = ReviewEvent(
                        chapter_id=chapter_id, reviewer_id=reviewer_id,
                        decision=review_decision,
                        feedback_text=resume_payload.get("feedback") or None,
                    )
                    s.add(rev)
                    # ⭐ D-BT:flush 拿 PK,SlotLost 补偿用 id 精准更新而不是
                    # "按 chapter_id 取最近一条";避免补偿延迟期间用户又提交了
                    # 新审核被错标 aborted
                    await s.flush()
                    review_event_id = rev.id
                    # ⭐ D-AZ:approve/skip **不切 generating**,保持 reviewing 直到
                    # update_state 节点切到最终的 approved/skipped(generating 语义是
                    # "正在写章节",approve/skip 不写所以不该用这个状态);
                    # 仅 revise 切 generating(下游 write_chapter 真的要重写)
                    if review_decision == "revise":
                        # ⭐ D-BK:revise 切 generating 同时写 processing_started_at,
                        # 保证 cleanup_stale_chapters 的 generating 分支能扫到
                        await s.execute(sa.text(
                            "UPDATE chapters SET status='generating', "
                            "processing_started_at=NOW() "
                            "WHERE id=:c AND status='reviewing'"
                        ), {"c": chapter_id})
                    await s.commit()

        async with project_heartbeat(project_id, token) as lost_event:
            async for _ in graph.astream(Command(resume=resume_payload), config,
                                         stream_mode="values"):
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during resume, project_id={project_id}")
    except SlotLost:
        # ⭐ D-AT + D-AW + D-AZ 补偿:把 decision 一起传入,让补偿区分语义
        # - approve/skip:章节回 awaiting_review(决策有效但 update_state 还没跑完;
        #                  让用户重提交一次,反正是同样的决策,语义安全);
        # - revise / 其它(retry / start):章节标 failed(确实可能已开始重写)
        # run 下所有 generating 章节统一标 failed(下一章场景)
        await _slot_lost_compensation(project_id, run_id, current_chapter_id=chapter_id,
                                      action="resume", decision=review_decision,
                                      review_event_id=review_event_id)
        log.warning("resume_review_task_aborted_slot_lost", project_id=project_id)
    except ChapterGenerationFailed as e:
        # ⭐ D-AU:resume 期间生成下一章失败 — project 切 awaiting_review,
        # chapter 已被节点同步 failed + last_error;不 raise
        await _set_project_status(project_id, "awaiting_review")
        log.info("resume_review_task_chapter_failed",
                 project_id=project_id, chapter_index=e.chapter_index)
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "resume_review_task crashed",
                               run_id=run_id, payload=resume_payload, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        # ⭐ D-BA:Run.status 同步,与 Project.status 一致
        await _fail_project_and_run(project_id, run_id, "resume_review_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
async def retry_failed_chapter_task(ctx, *, project_id: int, run_id: int, thread_id: str,
                                    chapter_index: int, reviewer_id: int,
                                    chapter_id: int, slot_token: str):
    """API 端点已 try_acquire 成功才入队;DB 重置 + 续跑;worker 入口写 ReviewEvent。
    ⚠️ D-AH:DB reset 与 ReviewEvent 都在 try 内,异常必释放 slot。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "retry")
    if token is None:
        return

    try:
        # ⭐ D-AC + D-AD:写 ReviewEvent + 把章节从 'retrying' 重置到 'pending'
        async with session_factory() as s:
            s.add(ReviewEvent(chapter_id=chapter_id, reviewer_id=reviewer_id,
                              decision="retry_failed"))
            await s.execute(sa.text(
                "UPDATE chapter_versions SET abandoned=true "
                "WHERE chapter_id=:c AND abandoned=false"
            ), {"c": chapter_id})
            # ⭐ D-BS:切 pending 同时写 processing_started_at,让 cleanup 能扫到
            # "retry 切完 pending 但 graph.astream 没启动就崩"的窗口
            await s.execute(sa.text(
                "UPDATE chapters SET status='pending', retry_count=0, last_error=NULL, "
                "processing_started_at=NOW() "
                "WHERE id=:c AND status='retrying'"
            ), {"c": chapter_id})
            await s.commit()

        async with project_heartbeat(project_id, token) as lost_event:
            await graph.aupdate_state(config, {"retry_count": 0})
            async for _ in graph.astream(None, config, stream_mode="values"):
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during retry, project_id={project_id}")
    except SlotLost:
        # ⭐ D-AT + D-AW 补偿:retry 已把章节 reset 到 pending,graph.astream 期间
        # 又切 generating;retry 语义就是"重写",SlotLost 时章节标 failed 让用户再 /retry。
        await _slot_lost_compensation(project_id, run_id, current_chapter_id=chapter_id,
                                      action="retry", decision=None)
        log.warning("retry_failed_chapter_task_aborted_slot_lost", project_id=project_id)
    except ChapterGenerationFailed as e:
        # ⭐ D-AU:retry 期间再次失败 — 章节仍 failed,project 切 awaiting_review;不 raise
        await _set_project_status(project_id, "awaiting_review")
        log.info("retry_failed_chapter_task_chapter_failed",
                 project_id=project_id, chapter_index=e.chapter_index)
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(pdir, "retry_failed_chapter_task crashed",
                               run_id=run_id, chapter_index=chapter_index, traceback=tb)
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        # ⭐ D-BA:Run.status 与 Project.status 同步标 failed
        await _fail_project_and_run(project_id, run_id, "retry_failed_chapter_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


async def _ensure_or_reacquire(project_id: int, slot_token: str,
                               run_id: int, action: str) -> str | None:
    """task 入口的 lease 校验。返回字符串 token 或 None。
    ⚠️ v3.5 把 try_acquire 返回类型改成 AcquireResult 后,这里要用 .acquired/.token,
    不能再 if-truthy 整个对象(对象永远 truthy)。

    1. token 仍匹配 → 返回原 token
    2. token 失效但有空名额 → 重新 acquire,返回新 token
    3. token 失效且没空名额 → 切项目状态回 queued / 失败回退,返回 None
    """
    if await ensure_project_slot(project_id, slot_token):
        return slot_token

    log.warning("slot_token_lost", project_id=project_id, action=action,
                run_id=run_id, hint="reservation TTL expired or reconciled")

    result = await try_acquire_project_slot(project_id)
    if result.acquired:
        log.info("slot_reacquired", project_id=project_id, action=action)
        return result.token

    # already_active 不该出现(自己已 release 后又 ensure 失败,SET 里应当不在)
    # 但如果发生,把它视作"被别的 task 占着",当前 task 应当退出
    if result.reason == "already_active":
        log.error("ensure_inconsistent_already_active", project_id=project_id,
                  action=action, hint="someone else acquired same project_id?")
        return None

    # 没空名额:把项目状态切回 queued(start) 或 不动(resume/retry,
    # 因为 /review /retry 端点已经把 chapter 切到中间态,worker 这里失败
    # 应让 cron reconcile_periodic 把项目标 failed,用户能看到错误并人工处理)
    async with session_factory() as s:
        if action == "start":
            await s.execute(sa.text(
                "UPDATE projects SET status='queued' WHERE id=:p"
            ), {"p": project_id})
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


async def _slot_lost_compensation(project_id: int, run_id: int,
                                  current_chapter_id: int | None,
                                  action: str,
                                  decision: str | None = None,
                                  review_event_id: int | None = None) -> None:
    """⭐ D-AW + D-AZ:SlotLost 路径的统一补偿。
    1. current_chapter_id 按 decision 决定回滚到哪:
       - approve / skip(D-AZ):决策已经下,只是 update_state 节点没跑完;
         **回到 awaiting_review**(让用户重新提交一次,语义安全)
       - revise / retry / start(无 decision 或 'revise'):**标 failed**
         (可能已开始重写,内容半成品,标 failed 让用户能再 /retry)
    2. run 下任意 generating 章节也标 failed(覆盖"resume 期间生成下一章被打断"
       与"retry 跑起来后 graph 切到 generating 又被打断"两种场景)
    3. project 状态:
       - 已进入章节循环(步 1 / 步 2 至少改动了一行)→ 切 awaiting_review
       - 仍在 extract/outline 阶段(start action 且无任何改动)→ 不动 project,
         让 cron `reconcile_periodic` 标 failed(那是真项目级失败)
    """
    rolled_back = 0
    try:
        async with session_factory() as s:
            if current_chapter_id is not None:
                if decision in ("approve", "skip"):
                    # ⭐ D-AZ:approve/skip 不写,SlotLost 时回 awaiting_review
                    # (let user re-submit;状态语义保持"待审核")
                    r1 = await s.execute(sa.text(
                        "UPDATE chapters SET status='awaiting_review', "
                        "processing_started_at=NULL "
                        "WHERE id=:c AND status='reviewing' "
                        "RETURNING id"
                    ), {"c": current_chapter_id})
                    chapter_rolled_back = r1.fetchall()
                    rolled_back += len(chapter_rolled_back)
                    # ⭐ D-BM:仅当章节真被回滚(rows 非空)才标 ReviewEvent aborted。
                    # 否则说明 update_state 已把章节切 approved/skipped,决策已经落库,
                    # 不应再把 ReviewEvent 标 aborted(那会让前端误以为这次审核没生效)
                    if chapter_rolled_back and review_event_id is not None:
                        # ⭐ D-BI + D-BT:精确按 review_event_id 标 aborted。
                        # 不再用"按 chapter_id 取最近一条":若补偿延迟期间用户
                        # 又提交了新审核,新事件会被错标 aborted。改用 worker
                        # 入口 flush 出来的具体 id 后,无论补偿何时跑都精准
                        await s.execute(sa.text(
                            "UPDATE review_events SET aborted=true "
                            "WHERE id=:rev_id AND aborted=false"
                        ), {"rev_id": review_event_id})
                    elif chapter_rolled_back:
                        # 兜底:review_event_id 缺失(不期望发生)走旧的 LIMIT 1 路径
                        log.warning("slot_lost_review_event_id_missing",
                                    chapter_id=current_chapter_id)
                        await s.execute(sa.text(
                            "UPDATE review_events SET aborted=true "
                            "WHERE id = ("
                            "  SELECT id FROM review_events "
                            "  WHERE chapter_id=:c AND aborted=false "
                            "  ORDER BY created_at DESC LIMIT 1"
                            ")"
                        ), {"c": current_chapter_id})
                else:
                    # revise / retry / start:reviewing(还没切)/ retrying / pending
                    # / generating(已切下游或重写中)→ 统一 failed
                    r1 = await s.execute(sa.text(
                        "UPDATE chapters SET status='failed', "
                        "processing_started_at=NULL, "
                        "last_error=COALESCE(NULLIF(last_error,''),'') || "
                        "  CASE WHEN COALESCE(last_error,'')='' THEN '' ELSE ' | ' END || "
                        "  'slot lost during ' || :a "
                        "WHERE id=:c "
                        "AND status IN ('reviewing','retrying','pending','generating') "
                        "RETURNING id"
                    ), {"c": current_chapter_id, "a": action})
                    rolled_back += len(r1.fetchall())
            # 兜底:run 下其它正在生成中的章节(下一章场景),不论 decision
            r2 = await s.execute(sa.text(
                "UPDATE chapters SET status='failed', "
                "processing_started_at=NULL, "
                "last_error='slot lost during chapter generation' "
                "WHERE run_id=:r AND status='generating' RETURNING id"
            ), {"r": run_id})
            rolled_back += len(r2.fetchall())
            await s.commit()
    except Exception:
        log.exception("slot_lost_chapter_rollback_failed",
                      project_id=project_id, run_id=run_id)

    # 决策 project 状态:有改动 → 已在 loop,切 awaiting_review;
    # 否则保持原样让 reconcile 标 failed(start 阶段卡 extract/outline)
    if rolled_back > 0:
        try:
            await _set_project_status(project_id, "awaiting_review")
        except Exception:
            log.exception("slot_lost_project_status_failed", project_id=project_id)
    else:
        log.warning("slot_lost_no_chapter_rolled_back",
                    project_id=project_id, action=action,
                    hint="left for reconcile to mark failed")


async def _fail_project_and_run(project_id: int, run_id: int, error: str) -> None:
    """⭐ D-BA:task 顶层 generic exception 时,Project 与 Run 一起标 failed。
    Run.error 字段最长 4000,截断保护;finished_at=NOW()。
    幂等:Run 已经是 done/failed/aborted 时不动(WHERE status='running')。
    """
    try:
        async with session_factory() as s:
            await s.execute(sa.text(
                "UPDATE projects SET status='failed' WHERE id=:p"
            ), {"p": project_id})
            await s.execute(sa.text(
                "UPDATE runs SET status='failed', "
                "finished_at=NOW(), error=:e "
                "WHERE id=:r AND status='running'"
            ), {"r": run_id, "e": error[:4000]})
            await s.commit()
    except Exception:
        log.exception("fail_project_and_run_failed",
                      project_id=project_id, run_id=run_id)
```

> 注:模块顶部加 `import traceback`。`append_error` 在写日志失败时已自吞异常,但保险起见外层也包一层 try,确保异常路径不会因日志失败而二次崩溃。

> `arq` `WorkerSettings.max_jobs` 设为 `MAX_CONCURRENT_PROJECTS + 2`(D-AA),给 DOCX task 留余量,workflow 业务限流仍由 ACTIVE_SET 主导。两层独立。

**API 端点的 acquire 责任**(`/start` `/review` `/confirm-outline` `/retry` 都要做):

```python
# 伪代码,真实端点见 §15;⚠️ 切记单次 try_acquire,第二次会变 already_active
result = await try_acquire_project_slot(project_id)
if result.reason == "already_active":
    raise HTTPException(409, "该项目已有任务在执行")
if not result.acquired:
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="并发上限已达,请 1 分钟后重试",
        headers={"Retry-After": "60"},
    )
try:
    await arq_pool.enqueue_job(..., slot_token=result.token)
except Exception:
    await release_project_slot(project_id, result.token)   # 补偿
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
    """⭐ D-BP:把允许写的字段限制在白名单内,防 caller 误传任意 key 拼出
    意外 SQL(SQLAlchemy text() 的 :param 绑定本身防 SQL 注入,但拼字段名段还是
    应当走显式白名单,避免静默写错列名 / 写到不该改的列)。
    """
    if not fields:
        return
    sql = _build_update_sql(fields)
    async with session_factory() as s:
        await s.execute(sa.text(sql), {"r": run_id, "i": index, **fields})
        await s.commit()


# ⭐ D-BP:章节级 sync 字段白名单。新增字段时往这里加;非白名单字段直接 raise
# 而不是静默忽略,因为后者会让"上游写错列名"在测试中沉默,生产又看不到。
_CHAPTER_SYNC_ALLOWED = frozenset({
    "status", "final_text", "last_error", "retry_count",
    "processing_started_at",  # D-AR / D-BF
})


def _build_update_sql(fields: dict) -> str:
    """根据 fields 生成 `UPDATE chapters SET k=:k, ... WHERE run_id=:r AND index=:i`。
    白名单限制 + 字典 key 必须是 Python 标识符(防异常字符)。"""
    bad = [k for k in fields if k not in _CHAPTER_SYNC_ALLOWED or not k.isidentifier()]
    if bad:
        raise ValueError(f"sync_chapter_to_db: disallowed fields: {bad}")
    set_clause = ", ".join(f"{k}=:{k}" for k in fields)
    return (f"UPDATE chapters SET {set_clause} "
            f"WHERE run_id=:r AND index=:i")


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
from pathlib import Path

import litellm
import sqlalchemy as sa
import structlog
from litellm.exceptions import RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout

from ..config import settings
from ..core.error_log import append_error    # ⭐ D-BE:LLM 重试日志直接写 errors.log
from ..db import session_factory
from ..events.bus import event_bus
from .token_usage import record_token_usage

log = structlog.get_logger()


class LLMRetryFailed(Exception):
    pass


class LLMTimeoutExceeded(Exception):
    pass


class ChapterGenerationFailed(Exception):
    """⭐ D-AU:章节级失败的语义化标记。

    write_chapter 节点(LLM-2)在 LLMRetryFailed / Timeout 后再包一层抛出,
    worker task 用 except ChapterGenerationFailed 区分:
    - **不**写 errors.log(节点已经把 chapter.last_error 同步到 DB,前端能看到)
    - **不** raise(arq max_tries=1,raise 会让 task 显示 failed,但语义不对)
    - project 状态切 'awaiting_review'(让用户能从 P5 看到 failed 章节并 /retry)

    与 LLM-1 / 抽取阶段失败的区分:那些抛 generic Exception → project failed,
    因为没有"章节级失败 → 用户重试单章"的概念。
    """
    def __init__(self, message: str, *, chapter_index: int, chapter_id: int | None = None):
        super().__init__(message)
        self.chapter_index = chapter_index
        self.chapter_id = chapter_id


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
    timeout_s = settings.single_chapter_timeout_seconds

    # ⭐ D-BG:总超时也要落 errors.log,**包在 try 外**捕 TimeoutError
    try:
        async with asyncio.timeout(timeout_s):
            for attempt in range(settings.llm_retry_max + 1):
                try:
                    return await _do_stream(model, messages, api_key, user_id, project_id,
                                            run_id, chapter_index, **kw)
                except (RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout) as e:
                    last_err = e
                    log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                    # ⭐ D-BE:把每次重试也写到 errors.log(FR-3.9 要求"重试日志记到 errors.log")
                    await _write_llm_error(project_id, f"LLM retry attempt={attempt}",
                                           model=model,
                                           error_type=type(e).__name__, error=str(e))
                    if attempt < settings.llm_retry_max:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    # 重试用尽,落"LLM exhausted"再抛
                    await _write_llm_error(project_id, "LLM exhausted",
                                           model=model, total_attempts=attempt + 1,
                                           last_error=str(e))
                    raise LLMRetryFailed(str(e)) from e
                except Exception:
                    # 4xx 等不重试
                    raise
    except TimeoutError as te:
        # ⭐ D-BG:外层 asyncio.timeout 触发的总超时(FR-3.10 = 600s 兜底)
        await _write_llm_error(project_id, "LLM total timeout",
                               model=model, timeout_seconds=timeout_s,
                               last_error=str(last_err) if last_err else None)
        raise LLMTimeoutExceeded(
            f"LLM stream exceeded {timeout_s}s total timeout"
        ) from te

    raise LLMRetryFailed(str(last_err))


async def _llm_project_dir(project_id: int) -> Path:
    """D-BE:取项目目录给 errors.log 用;查询失败返回 None,调用方需保护。"""
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"), {"p": project_id},
        )
        return Path(row.scalar_one())


async def _write_llm_error(project_id: int, message: str, **fields) -> None:
    """⭐ D-BE:LLM 错误日志写入项目级 errors.log,失败永不传播。"""
    try:
        pdir = await _llm_project_dir(project_id)
        await append_error(pdir, message, **fields)
    except Exception:
        log.exception("llm_error_log_write_failed",
                      project_id=project_id, message=message)


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

    # ⭐ D-BG:JSON 模型也要 errors.log + 总超时分支
    try:
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
                    # ⭐ D-BO:JSON 解析失败也写 errors.log(D-BG 漏的盲区)
                    last_err = je
                    await _write_llm_error(project_id,
                                           f"LLM retry attempt={attempt}",
                                           model=model, mode="json",
                                           error_type="JSONDecodeError",
                                           error=str(je),
                                           content_head=content[:200])
                    raise LLMRetryFailed(f"json parse: {je}") from je

                return parsed, StreamResult(text=content,
                                            prompt_tokens=p_tok,
                                            completion_tokens=c_tok)

            except (RateLimitError, ServiceUnavailableError, APIConnectionError, Timeout) as e:
                last_err = e
                log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                # ⭐ D-BG:JSON 模型也写 errors.log
                await _write_llm_error(project_id, f"LLM retry attempt={attempt}",
                                       model=model, mode="json",
                                       error_type=type(e).__name__, error=str(e))
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                await _write_llm_error(project_id, "LLM exhausted",
                                       model=model, mode="json",
                                       total_attempts=attempt + 1, last_error=str(e))
                raise LLMRetryFailed(str(e)) from e
            except LLMRetryFailed:
                # JSON 解析失败也走重试链(模型偶尔吐非 JSON);
                # D-BO 的 errors.log 已经在 raise 前写过 attempt 行,这里重试只 sleep
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                # 重试用尽 → 落 LLM exhausted(JSON 路径)
                await _write_llm_error(project_id, "LLM exhausted",
                                       model=model, mode="json",
                                       total_attempts=attempt + 1,
                                       last_error=str(last_err) if last_err else "json parse")
                raise
    except TimeoutError as te:
        # ⭐ D-BG:JSON 模型外层总超时(默认 120s)也写 errors.log
        await _write_llm_error(project_id, "LLM total timeout",
                               model=model, mode="json",
                               timeout_seconds=timeout,
                               last_error=str(last_err) if last_err else None)
        raise LLMTimeoutExceeded(
            f"LLM JSON exceeded {timeout}s total timeout"
        ) from te

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
from datetime import datetime, timezone
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...models import Project, ApiKey
from ...core.crypto import decrypt_api_key
from ..prompts.llm2_chapter import build_messages
from ..state import WorkflowState
from ..sync import sync_chapter_to_db, publish_event
from ...services.llm import (
    call_llm_stream, LLMRetryFailed, LLMTimeoutExceeded, ChapterGenerationFailed,
)


async def run(state: WorkflowState) -> dict:
    current = state["current_index"]
    chapter = state["chapters"][current]

    # API Key 实时取
    api_key = await _resolve_api_key(state["project_id"])

    # 通知前端章节开始
    # ⭐ D-BF:切 generating 同时写 processing_started_at,让 cron `cleanup_stale_chapters`
    # 能在 worker 进程被 SIGKILL / OOM 直接死时(不会进 SlotLost 分支)兜底回滚
    await sync_chapter_to_db(state["run_id"], current,
                             status="generating",
                             processing_started_at=datetime.now(timezone.utc))
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
    except (LLMRetryFailed, LLMTimeoutExceeded, asyncio.TimeoutError) as e:
        # D-BG:call_llm_stream 总超时已经被包成 LLMTimeoutExceeded,
        # 这里同时 catch asyncio.TimeoutError 是兜底(防 _do_stream 内部别处冒泡)
        await sync_chapter_to_db(state["run_id"], current,
                                 status="failed", last_error=str(e),
                                 processing_started_at=None)
        await publish_event(state["project_id"], "chapter_failed",
                            chapter_index=current, reason=str(e))
        # ⭐ D-AU:用语义化异常,worker task 据此把 project 切 awaiting_review
        # 而不是 failed(只是当前章节失败,工作流暂停等用户 /retry)
        raise ChapterGenerationFailed(
            str(e), chapter_index=current,
            chapter_id=await _resolve_chapter_id(state["run_id"], current),
        ) from e

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


async def _resolve_chapter_id(run_id: int, index: int) -> int | None:
    """D-AU:抛 ChapterGenerationFailed 时一并带上 chapter_id,
    worker task 直接用,免再查;查不到返回 None(几乎不会发生)。"""
    async with session_factory() as s:
        row = await s.execute(sa.text(
            "SELECT id FROM chapters WHERE run_id=:r AND index=:i"
        ), {"r": run_id, "i": index})
        return row.scalar_one_or_none()
```

> 注:`write_chapter.py` 顶部需 `import sqlalchemy as sa` 与 `from sqlalchemy import select`(只 select 不够,_resolve_chapter_id 用了 `sa.text`)。

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
_MODULE_LOCK_TIMEOUT = 120         # ⭐ D-BR:同进程锁等待上限,与 Redis blocking 一致
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
    on_stage=None,   # ⭐ D-BD:可选回调,签名 async def on_stage(stage: str) -> None
    job_id: int | None = None,   # ⭐ D-BN:tmp 文件名后缀,防并发同名(实际上 partial unique 已防)
) -> Path:
    """串行化包装。**返回临时 .tmp.docx 路径**,由调用方决定是否 atomic rename
    成 `proposal.docx` 终态文件(D-BN)。

    ⭐ D-BD:`on_stage` 回调让上层 task 在 mermaid 完成、进入 pandoc 阶段时
    update DocxJob.status='pandoc',与 §8 status 字段定义对齐。

    ⭐ D-BN:写到 `proposal.{job_id}.tmp.docx`,而不是直接覆盖 `proposal.docx`。
    原因:cron `cleanup_stale_docx_jobs` 可能在 task 等串行锁期间把 DocxJob 标 failed,
    若直接写终态 `proposal.docx`,前端 GET `/proposal.docx` 看 `cached file exists`
    就直接返回(IMPLEMENTATION_SPEC §15.3 缓存命中逻辑),把不完整 / 已 failed 的产物展示出来。

    ⭐ D-BR:`_module_lock` 加 `wait_for(timeout=120s)`。原 `async with _module_lock`
    没有超时,多个 DOCX task 同时入队 → 全部占着 arq worker job slot 等本地锁 →
    workflow task 入队后排在 arq 队列后面饿死。即使 D-AA 把 max_jobs 设为
    `max_concurrent_projects + 2`,DOCX 数量超过 +2 仍会拥塞。timeout 让多余的
    DOCX 主动失败(用户可重试),不再无限制占 worker。
    """
    # D-BR:同进程锁有等待上限,超时直接 raise → task 标 failed
    try:
        await asyncio.wait_for(_module_lock.acquire(), timeout=_MODULE_LOCK_TIMEOUT)
    except asyncio.TimeoutError as te:
        raise TimeoutError(
            f"docx module lock timeout after {_MODULE_LOCK_TIMEOUT}s"
        ) from te
    try:
        async with _redis_lock(redis_url):  # 跨进程(未来扩容)
            return await _export_docx_inner(markdown, project_dir, reference_doc,
                                            on_stage=on_stage, job_id=job_id)
    finally:
        _module_lock.release()


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
                             reference_doc: Path, *,
                             on_stage=None, job_id: int | None = None) -> Path:
    work = project_dir / "docx-build"
    work.mkdir(parents=True, exist_ok=True)

    # 1. mermaid 预渲染(图片用相对路径,后面 pandoc 用 --resource-path 解析)
    inlined = await _render_mermaid(markdown, work)

    md_path = work / "proposal_inlined.md"
    md_path.write_text(inlined, encoding="utf-8")

    # ⭐ D-BN:写临时文件;调用方在 DB done 成功后做 atomic rename
    # job_id 后缀让多个并发 cleanup → retry 周期不会冲突(虽然 partial unique 已防同时;
    # 这里再多一层防御:即便 partial unique 失效也不会复写到错误的 tmp)
    suffix = f".{job_id}" if job_id is not None else ""
    out_path = project_dir / f"proposal{suffix}.tmp.docx"

    # ⭐ D-BD + D-CB:mermaid 完毕,通知上层切 status='pandoc';
    # **不再 catch 异常**——on_stage 抛 _StaleJob(D-BX) 必须透传到 task 顶层,
    # 否则信号被吞掉,pandoc 仍会继续跑,失去 cleanup → 阶段守护链路的意义。
    # on_stage 内部已经做了显式 log.warning,不需要在这里再 try/except 兜底。
    if on_stage is not None:
        await on_stage("pandoc")

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


@func(max_tries=1)   # ⭐ D-AY:与其他 task 一致;失败由用户重新 POST /render-docx 触发新 DocxJob
async def generate_docx_task(ctx, *, project_id: int, docx_job_id: int) -> dict:
    """串行锁在 export_docx 内部实现(D-H)。"""
    # 0. ⭐ D-AK:校验 DocxJob row 存在(防 v3.4 enqueue/commit 缺口);失败直接退出
    async with session_factory() as s:
        job_row = await s.execute(
            sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
            {"i": docx_job_id},
        )
        existing = job_row.scalar_one_or_none()
        if existing is None:
            log.error("docx_job_row_missing", docx_job_id=docx_job_id,
                      project_id=project_id,
                      hint="API 端 commit 可能失败;arq 仍把 task 入了队")
            return {"error": "docx_job row not found"}
        if existing in ("done", "failed"):
            log.warning("docx_job_already_finished",
                        docx_job_id=docx_job_id, status=existing)
            return {"status": existing}
        # ⭐ D-CM:invalidated 守护 — assemble 节点已经把本 job 作废
        # (markdown 重生成期间正好 in-flight 的 task),不再继续推进
        if existing == "invalidated":
            log.info("docx_job_already_invalidated",
                     docx_job_id=docx_job_id,
                     hint="markdown 重生成,本任务的产物不再有效")
            return {"status": "invalidated"}

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

    # 2. ⭐ D-BX:进 rendering_mermaid 加 WHERE status='pending' 前置;
    # rowcount==0 说明已被 cleanup 标 failed,task 应当退出而不是继续推进
    class _StaleJob(Exception):
        """阶段切换发现 cleanup 已抢标 failed,放弃任务。"""
    async with session_factory() as s:
        result = await s.execute(
            sa.text("UPDATE docx_jobs SET status='rendering_mermaid', "
                    "updated_at=NOW() WHERE id=:i AND status='pending'"),
            {"i": docx_job_id},
        )
        await s.commit()
        if result.rowcount == 0:
            log.warning("docx_stage_blocked_at_rendering",
                        docx_job_id=docx_job_id,
                        hint="cleanup 已把 job 标 failed;不再启动 mermaid 渲染")
            return {"status": "stale", "output_path": None}

    # ⭐ D-CU:进 rendering 阶段后,**立即强制 unlink 旧 final_path**(若存在)
    # 这是 finalizing repair 安全性的关键前提:让"finalizing 期间 final_path
    # 存在 ⇔ 当前 task 已 atomic rename"严格成立。否则旧 job(invalidated 后
    # unlink 失败 / 用户手动复制)残留的 proposal.docx,会让
    # **四处 finalizing repair**(cleanup / POST cached / GET /docx-job /
    # GET 下载,见 D-BY / D-CJ POST / D-CD / D-CO)把当前 task 错误标 done,
    # 实际下载的是旧 DOCX
    # unlink 失败:UPDATE failed + raise,本次 task 退出,前端可重试
    # missing_ok=True:文件不存在不算失败(常态)
    final_path_to_clear = project_dir / "proposal.docx"
    try:
        final_path_to_clear.unlink(missing_ok=True)
    except OSError as e:
        log.exception("docx_pre_render_unlink_failed",
                      docx_job_id=docx_job_id, path=str(final_path_to_clear))
        async with session_factory() as s:
            await s.execute(sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error=:e, finished_at=NOW(), updated_at=NOW() "
                "WHERE id=:i AND status='rendering_mermaid'"
            ), {"i": docx_job_id, "e": f"failed to clear stale final: {e!r}"[:4000]})
            await s.commit()
        raise

    # ⭐ D-BD + D-BH + D-BX:on_stage 切换 WHERE status 前置 + rowcount 守护;
    # cleanup 抢标 failed 时 raise _StaleJob 让 export_docx 立刻退出,task 收到后
    # unlink tmp 退出。allowed_from 描述"该阶段的合法前驱状态"
    _ALLOWED_FROM = {
        "pandoc": ("rendering_mermaid",),
    }
    async def _update_stage(stage: str) -> None:
        prev_states = _ALLOWED_FROM.get(stage)
        if not prev_states:
            raise ValueError(f"unknown stage: {stage}")
        in_clause = ",".join(f"'{s}'" for s in prev_states)
        async with session_factory() as s:
            r = await s.execute(
                sa.text(f"UPDATE docx_jobs SET status=:s, updated_at=NOW() "
                        f"WHERE id=:i AND status IN ({in_clause})"),
                {"s": stage, "i": docx_job_id},
            )
            await s.commit()
            if r.rowcount == 0:
                log.warning("docx_stage_blocked",
                            docx_job_id=docx_job_id, stage=stage)
                raise _StaleJob(stage)

    # 3. 真正执行(锁在 export_docx 内,产物写入 tmp 路径)
    final_path = project_dir / "proposal.docx"     # ⭐ D-L 固定缓存名(终态)
    try:
        tmp_path = await export_docx(
            markdown=markdown,
            project_dir=project_dir,
            project_name=project_name,
            reference_doc=Path(settings.templates_dir) / "reference.docx",
            redis_url=settings.redis_url,
            on_stage=_update_stage,
            job_id=docx_job_id,                      # ⭐ D-BN:tmp 文件名后缀
        )
    except _StaleJob as se:
        # ⭐ D-BX:cleanup 抢标 failed,清理半成品 tmp(若已生成)并放弃任务,
        # 不再 raise 给 arq(arq 会显示 task failed,但 DB 已 failed,无需再标)
        log.info("docx_task_stale_exit",
                 docx_job_id=docx_job_id, stage=str(se))
        # 注:tmp 文件路径由 _export_docx_inner 决定(proposal.{job_id}.tmp.docx),
        # 由于 _StaleJob 是 on_stage 内部抛的,tmp 文件可能已经生成完成
        # 也可能还没生成完成;统一尝试 unlink
        try:
            (project_dir / f"proposal.{docx_job_id}.tmp.docx").unlink(missing_ok=True)
        except Exception:
            log.exception("docx_tmp_unlink_failed", docx_job_id=docx_job_id)
        return {"status": "stale", "output_path": None}
    except Exception as e:
        # ⭐ D-BH + D-BQ:WHERE 覆盖所有 in-flight 防覆盖 cleanup 抢标的行
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE docx_jobs SET status='failed', error=:e, "
                        "finished_at=NOW(), updated_at=NOW() "
                        "WHERE id=:i AND status IN "
                        "('pending','rendering_mermaid','pandoc','finalizing')"),
                {"i": docx_job_id, "e": str(e)[:4000]},
            )
            await s.commit()
        raise

    # ⭐ D-BQ:抢占 finalizing 状态;cleanup 不会再覆盖,但 rowcount==0 说明
    # 已被 cleanup 标 failed,本次产物丢弃。先抢 finalizing 再 rename 再 done,
    # 避免 v3.11 的"先 done 再 rename 之间崩溃 → DB done 但文件不存在 → 下载 409"
    async with session_factory() as s:
        result = await s.execute(
            sa.text("UPDATE docx_jobs SET status='finalizing', updated_at=NOW() "
                    "WHERE id=:i AND status IN "
                    "('pending','rendering_mermaid','pandoc')"),
            {"i": docx_job_id},
        )
        await s.commit()
        if result.rowcount == 0:
            # 已被 cleanup 抢标 failed;丢弃 tmp 文件
            log.warning("docx_finalize_blocked",
                        docx_job_id=docx_job_id,
                        hint="cleanup 已把 job 标 failed;丢弃 tmp 产出")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                log.exception("docx_tmp_unlink_failed", tmp=str(tmp_path))
            return {"status": "stale", "output_path": None}

    # ⭐ D-CQ:rename 前再查一次 status 防御性 — assemble 节点可能在 finalizing
    # 抢占 / rename 之间把 status 改 invalidated(D-CM)。读到 invalidated 时不
    # rename,直接清 tmp 退出,避免无效产物落到终态路径(虽然 _commit_done 后续
    # 也会清,但提早识别更省 IO + log 更清晰)
    async with session_factory() as s:
        cur = (await s.execute(
            sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
            {"i": docx_job_id},
        )).scalar_one_or_none()
    if cur == "invalidated":
        log.info("docx_skip_rename_invalidated_during_finalize",
                 docx_job_id=docx_job_id)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            log.exception("docx_tmp_unlink_failed", tmp=str(tmp_path))
        return {"status": "invalidated", "output_path": None}

    # ⭐ D-BN + D-BQ:finalizing 之后才 atomic rename;rename 在 done 之前
    try:
        tmp_path.rename(final_path)
    except Exception:
        # rename 失败:把 DB 标 failed,清 tmp(状态守恒 finalizing → failed)
        log.exception("docx_atomic_rename_failed",
                      tmp=str(tmp_path), final=str(final_path))
        async with session_factory() as s:
            await s.execute(sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='atomic rename failed', finished_at=NOW(), updated_at=NOW(), "
                "output_path=NULL WHERE id=:i AND status='finalizing'"
            ), {"i": docx_job_id})
            await s.commit()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    # ⭐ D-BQ + D-CE + D-CL:rename 成功才 commit done。抽 `_commit_done`
    # helper 既清理 task 主体也方便单元测试(D-CH/D-CL 用例直接调它)
    return await _commit_done(docx_job_id, final_path)


async def _commit_done(docx_job_id: int, final_path: Path) -> dict:
    """⭐ D-CL + D-CQ + D-CV:从 generate_docx_task 抽出的"finalizing → done"提交逻辑。

    **不变量(D-CV 修订)**:DB 是 source of truth;文件残留是 best-effort cleanup
    失败的回退,API 层(D-CJ)以 latest job 状态决定是否放行下载。本函数 invalidated
    分支尝试 unlink final_path 是 best-effort,失败时只 log 不再阻断 — 下一次新 job
    在 rendering 阶段会再被 D-CU 强制 unlink,形成自愈链路。

    检查 rowcount:WHERE status='finalizing' 命中 → 标准成功路径;
    命中 0 行 → SELECT 当前状态分类:
    - done(D-BY/D-CD 抢先 repair):log info 静默;
    - **invalidated**(D-CM assemble 抢标):best-effort unlink final_path,
      失败也不阻断(D-CU 兜底);
    - 其它(failed):log warning + 返回 stale,不删 final_path(留作排查证据)。"""
    async with session_factory() as s:
        result = await s.execute(
            sa.text("UPDATE docx_jobs SET status='done', output_path=:p, "
                    "finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='finalizing'"),
            {"i": docx_job_id, "p": str(final_path)},
        )
        await s.commit()
        if result.rowcount == 0:
            cur = (await s.execute(
                sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
                {"i": docx_job_id},
            )).scalar_one_or_none()
            if cur == "done":
                log.info("docx_done_already_repaired",
                         docx_job_id=docx_job_id,
                         hint="D-BY/D-CD 抢先修复,本 task 不再重复写")
            elif cur == "invalidated":
                # ⭐ D-CQ:assemble 在 rename 之后 commit 之前抢标 invalidated;
                # 文件已 rename 到 final_path,但 DB 已经作废 → unlink 保不变量
                log.info("docx_invalidated_during_commit_unlink",
                         docx_job_id=docx_job_id)
                try:
                    final_path.unlink(missing_ok=True)
                except Exception:
                    log.exception("docx_invalidated_unlink_failed",
                                  path=str(final_path))
                return {"status": "invalidated", "output_path": None}
            else:
                log.warning("docx_done_status_diverged",
                            docx_job_id=docx_job_id, current_status=cur,
                            hint="finalizing 期间被改走(failed),"
                                 "文件已 rename 但 DB 不是 done")
                return {"status": "stale", "output_path": str(final_path)}

    return {"output_path": str(final_path)}
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
    """启动工作流。**真快照**当前用户的 ApiKey 加密载荷到 Project(D-C)。
    ⭐ D-AF:校验 project.status=='init',防止重复启动同一项目导致 try_acquire
    返回 already_active 或 LangGraph thread_id 撞上历史 run。"""
    project = await _get_project_owned_or_412(db, project_id, user)

    # 状态校验:必须是 init(刚创建,还未 /start)
    if project.status != "init":
        raise HTTPException(409,
            f"project status is '{project.status}', /start only allowed when init "
            "(已启动的项目使用 /resume 或在前端继续审核;失败的项目请 admin 处理)")

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

    # ⭐ D-T / D-AF:三态结果
    result = await try_acquire_project_slot(project_id)
    if result.reason == "already_active":
        # 不应该到这——上面已校验 status==init,但兜底
        raise HTTPException(409, "项目已有进行中的执行")

    if result.acquired:
        project.status = "extracting"
    else:  # "full"
        project.status = "queued"
    await db.commit()

    # 占名额成功才入队;失败 → 补偿(D-U):回退 status + 标 Run aborted + release slot
    if result.acquired:
        arq_pool = request.app.state.arq_pool
        try:
            await arq_pool.enqueue_job(
                "start_workflow_task",
                project_id=project_id, run_id=run.id, thread_id=thread_id,
                slot_token=result.token,
            )
        except Exception as e:
            log.exception("start_enqueue_failed", project_id=project_id, run_id=run.id)
            project.status = "init"
            run.status = "aborted"
            run.finished_at = datetime.now(timezone.utc)
            run.error = f"enqueue failed: {e!r}"
            await db.commit()
            await release_project_slot(project_id, result.token)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="无法入队工作流任务,请稍后重试 /start",
            ) from e
    return {"run_id": run.id, "queued": not result.acquired}


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

    result = await try_acquire_project_slot(project_id)
    if result.reason == "already_active":
        raise HTTPException(409, "该项目已有任务在执行")
    if not result.acquired:
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
            slot_token=result.token,
        )
    except Exception:
        await release_project_slot(project_id, result.token)
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

    # ⭐ D-AD 中间态 + D-AO 补偿全包 + D-AR processing_started_at
    chapter_id = chapter["id"]
    await db.execute(
        sa.text("UPDATE chapters SET status='reviewing', "
                "processing_started_at=NOW() WHERE id=:c"),
        {"c": chapter_id},
    )
    # 释放行锁(不等 acquire 内部失败再补偿;acquire 失败也只是状态切回)
    await db.commit()

    acquired_token: str | None = None
    try:
        result = await try_acquire_project_slot(project_id)
        if result.reason == "already_active":
            raise HTTPException(409, "该项目已有任务在执行,请稍后重试")
        if not result.acquired:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="系统繁忙(并发上限已达),请稍后重试",
                headers={"Retry-After": "60"},
            )
        acquired_token = result.token

        arq_pool = request.app.state.arq_pool
        await arq_pool.enqueue_job(
            "resume_review_task",
            project_id=project_id, run_id=run.id,
            thread_id=run.langgraph_thread_id,
            resume_payload={
                "kind": "chapter_review",
                "decision": body.decision,
                "feedback": body.feedback or "",
            },
            slot_token=acquired_token,
            reviewer_id=user.id,
            chapter_id=chapter_id,
        )
    except HTTPException:
        # 补偿:章节回 awaiting_review;若已 acquire 释放
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text("UPDATE chapters SET status='awaiting_review', "
                    "processing_started_at=NULL "
                    "WHERE id=:c AND status='reviewing'"),
            {"c": chapter_id},
        )
        await db.commit()
        raise
    except Exception as e:
        # Redis / SQLAlchemy / arq 等运行时异常:同样补偿
        log.exception("review_unexpected_error", project_id=project_id,
                      chapter_id=chapter_id)
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text("UPDATE chapters SET status='awaiting_review', "
                    "processing_started_at=NULL "
                    "WHERE id=:c AND status='reviewing'"),
            {"c": chapter_id},
        )
        await db.commit()
        raise HTTPException(503, "审核处理异常,请稍后重试") from e

    # ⭐ D-AC:ReviewEvent 由 worker 入口写,API 不再写
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

    # 中间态(D-AD)+ 补偿全包(D-AO)+ D-AR
    chapter_id = chapter["id"]
    await db.execute(
        sa.text("UPDATE chapters SET status='retrying', "
                "processing_started_at=NOW() WHERE id=:c"),
        {"c": chapter_id},
    )
    await db.commit()

    acquired_token: str | None = None
    try:
        result = await try_acquire_project_slot(project_id)
        if result.reason == "already_active":
            raise HTTPException(409, "该项目已有任务在执行,请稍后重试")
        if not result.acquired:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="系统繁忙,请稍后重试",
                headers={"Retry-After": "60"},
            )
        acquired_token = result.token

        arq_pool = request.app.state.arq_pool
        await arq_pool.enqueue_job(
            "retry_failed_chapter_task",
            project_id=project_id, run_id=run.id,
            thread_id=run.langgraph_thread_id,
            chapter_index=idx,
            chapter_id=chapter_id,
            reviewer_id=user.id,
            slot_token=acquired_token,
        )
    except HTTPException:
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text("UPDATE chapters SET status='failed', "
                    "processing_started_at=NULL "
                    "WHERE id=:c AND status='retrying'"),
            {"c": chapter_id},
        )
        await db.commit()
        raise
    except Exception as e:
        log.exception("retry_unexpected_error", project_id=project_id,
                      chapter_id=chapter_id)
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text("UPDATE chapters SET status='failed', "
                    "processing_started_at=NULL "
                    "WHERE id=:c AND status='retrying'"),
            {"c": chapter_id},
        )
        await db.commit()
        raise HTTPException(503, "重试处理异常,请稍后重试") from e

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

    # ⭐ D-BY:命中缓存前先 repair 任何"finalizing 但文件已就位"的孤儿 job
    cached = Path(project.dir_path) / "proposal.docx"
    if cached.exists():
        await db.execute(sa.text(
            "UPDATE docx_jobs SET status='done', output_path=:p, "
            "finished_at=NOW(), updated_at=NOW() "
            "WHERE project_id=:pid AND status='finalizing'"
        ), {"p": str(cached), "pid": project_id})
        await db.commit()

    # ⭐ D-CJ:**仅看文件存在不够** — invalidated 状态下旧文件可能残留(unlink
    # 失败 / 人为留),必须 DB 把关。查 latest DocxJob,只有 done 才命中缓存;
    # invalidated 走"新建 job"路径(下面 INSERT pending);其它 in-flight 就让
    # partial unique index 阻断。
    # ⭐ D-CK:cached=True 时返回 latest done 的 docx_job_id,前端有了轮询入口,
    # 后续 markdown 重生成把它改成 invalidated 时前端能看到
    latest = (await db.execute(sa.text(
        "SELECT id, status, output_path FROM docx_jobs "
        "WHERE project_id=:p ORDER BY id DESC LIMIT 1"
    ), {"p": project_id})).mappings().one_or_none()

    if cached.exists() and latest and latest["status"] == "done":
        return {
            "docx_job_id": latest["id"],
            "arq_job_id": None,
            "cached": True,
        }
    # latest=invalidated 或 latest 不存在 / 其它状态 → 走 INSERT pending 新建

    # ⭐ D-AK 顺序修正:先 commit pending 行,再 enqueue,最后 update arq_job_id。
    # v3.4 是 flush→enqueue→commit:enqueue 成功但 commit 失败时 worker 拿到 docx_job_id
    # 但 DB 没行,worker 入口 SELECT 找不到。
    from sqlalchemy.exc import IntegrityError
    docx_job = DocxJob(project_id=project_id, arq_job_id=None, status="pending")
    db.add(docx_job)
    try:
        await db.commit()                # ⭐ 先 commit,DB 行已可见
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "该项目已有 DOCX 生成任务在进行中")
    job_pk = docx_job.id

    arq_pool = request.app.state.arq_pool
    try:
        job = await arq_pool.enqueue_job(
            "generate_docx_task",
            project_id=project_id, docx_job_id=job_pk,
        )
    except Exception as e:
        # 补偿:enqueue 失败把 row 标 failed,前端可重试
        await db.execute(sa.text(
            "UPDATE docx_jobs SET status='failed', error=:err, finished_at=NOW() "
            "WHERE id=:i"
        ), {"err": f"enqueue failed: {e!r}", "i": job_pk})
        await db.commit()
        raise HTTPException(503, "无法入队 DOCX 任务,请稍后重试")

    if job is None:
        # arq 可能因为 keep_alive 等条件返回 None
        await db.execute(sa.text(
            "UPDATE docx_jobs SET status='failed', error='enqueue returned None', "
            "finished_at=NOW() WHERE id=:i"
        ), {"i": job_pk})
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "无法入队 DOCX 任务,请稍后重试")

    # 回写 arq_job_id;如果这一步失败也 OK——worker 仍能用 job_pk 找到 row
    docx_job_2 = await db.get(DocxJob, job_pk)
    if docx_job_2 is not None:
        docx_job_2.arq_job_id = job.job_id
        await db.commit()

    # ⭐ D-CC:对外统一用 docx_job_id(DB PK)作为前端轮询路径参数;
    # arq 的 job id 单独叫 arq_job_id,避免前端拿错 id 去 GET /docx-job/{id}
    return {"docx_job_id": job_pk, "arq_job_id": job.job_id, "cached": False}


# ⭐ D-BW:GET DOCX 任务进度 — REQUIREMENTS.md FR-5/§9 列了端点但 v3.12 之前缺实现。
# 前端轮询此端点了解 DOCX 生成进度。`finalizing` 是实现层内部态,
# 对前端**映射成 `processing`**(v3.12 D-BU 把 finalizing 标"实现层")
@router.get("/{project_id}/docx-job/{docx_job_id}")
async def get_docx_job(
    project_id: int,
    docx_job_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(sa.text(
        "SELECT id, project_id, status, error, output_path, "
        "created_at, updated_at, finished_at "
        "FROM docx_jobs WHERE id=:i AND project_id=:p"
    ), {"i": docx_job_id, "p": project_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "docx job not found")

    # ⭐ D-CD:轮询路径上 inline finalizing repair。前端最常走 GET 轮询;
    # 若 task 已 atomic rename 但 done UPDATE 失败,DB 卡 finalizing,
    # 前端会一直看 processing 直到 cron 跑(最多 5min)。这里看到
    # finalizing + 文件存在 → 立即修复成 done,延迟从 5min 降到 1 个轮询周期
    if row["status"] == "finalizing":
        proj_row = await db.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"), {"p": project_id},
        )
        dir_path = proj_row.scalar_one_or_none()
        if dir_path:
            file_path = Path(dir_path) / "proposal.docx"
            if file_path.exists():
                upd = await db.execute(sa.text(
                    "UPDATE docx_jobs SET status='done', "
                    "output_path=:p, finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='finalizing' RETURNING id, status, "
                    "output_path, finished_at, updated_at"
                ), {"i": docx_job_id, "p": str(file_path)})
                repaired = upd.mappings().first()
                if repaired:
                    await db.commit()
                    log.info("docx_finalizing_repaired_inline",
                             docx_job_id=docx_job_id, project_id=project_id)
                    # 用 repair 后的最新行覆盖 row,避免下方还按 finalizing 映射
                    row = {
                        **dict(row),
                        "status": repaired["status"],
                        "output_path": repaired["output_path"],
                        "finished_at": repaired["finished_at"],
                        "updated_at": repaired["updated_at"],
                    }

    # 内部 → 前端的 status 映射:不暴露 finalizing(实现层细节)
    raw = row["status"]
    public_status = "processing" if raw in (
        "pending", "rendering_mermaid", "pandoc", "finalizing"
    ) else raw   # done | failed | invalidated(D-CG)
    progress_hint = {
        "pending": "排队中",
        "rendering_mermaid": "渲染流程图...",
        "pandoc": "转换文档...",
        "finalizing": "收尾中...",
        "done": "已完成",
        "failed": "失败",
        # ⭐ D-CG:作废原因明确告诉用户,引导其重新触发生成
        "invalidated": "原文档已更新,请重新生成 DOCX",
    }.get(raw, raw)

    return {
        "docx_job_id": row["id"],
        "status": public_status,
        "stage": progress_hint,        # 给前端展示的中文短语
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


@router.get("/{project_id}/proposal.docx")
async def download_docx(
    project_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_done_project(db, project_id)
    path = Path(project.dir_path) / "proposal.docx"   # ⭐ D-L 固定缓存名

    # ⭐ D-CO:下载端 inline finalizing repair。task rename 成功但 done UPDATE
    # 失败时,latest 卡 finalizing,download 也应能修复(否则用户直接点下载会
    # 看到 docx_not_ready 直到 cron / GET 轮询触发 repair)。复用 D-CD 路径。
    if path.exists():
        await db.execute(sa.text(
            "UPDATE docx_jobs SET status='done', output_path=:p, "
            "finished_at=NOW(), updated_at=NOW() "
            "WHERE project_id=:pid AND status='finalizing'"
        ), {"p": str(path), "pid": project_id})
        await db.commit()

    # ⭐ D-CJ:文件存在 ≠ 可下载;先查 latest DocxJob 状态把关
    latest = (await db.execute(sa.text(
        "SELECT id, status FROM docx_jobs "
        "WHERE project_id=:p ORDER BY id DESC LIMIT 1"
    ), {"p": project_id})).mappings().one_or_none()

    # 拒分支 1:latest 是 invalidated → 旧 markdown 的产物已作废,
    # 不论文件是否还在都不放行;返回结构化 409 让前端展示重新生成入口
    if latest and latest["status"] == "invalidated":
        raise HTTPException(
            status_code=409,
            detail={"code": "docx_invalidated",
                    "message": "原文档已更新,请重新生成 DOCX",
                    "docx_job_id": latest["id"]},
        )

    # 拒分支 2:latest 不是 done(pending/in-flight/failed)→ 还没生成成功 / 生成失败
    if not latest or latest["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail={"code": "docx_not_ready",
                    "message": "请先 POST 触发生成",
                    "docx_job_id": latest["id"] if latest else None,
                    "current_status": latest["status"] if latest else None},
        )

    # latest=done,但文件不在(catastrophic):自动 repair latest 为 failed,前端能重新触发
    if not path.exists():
        await db.execute(sa.text(
            "UPDATE docx_jobs SET status='failed', "
            "error='done file missing on disk', finished_at=NOW(), updated_at=NOW(), "
            "output_path=NULL WHERE id=:i"
        ), {"i": latest["id"]})
        await db.commit()
        log.warning("docx_done_file_missing_repaired",
                    project_id=project_id, docx_job_id=latest["id"])
        raise HTTPException(
            status_code=409,
            detail={"code": "docx_missing",
                    "message": "DOCX 文件丢失,请重新生成",
                    "docx_job_id": latest["id"]},
        )

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
from ..services.concurrency import (
    reconcile_periodic, cleanup_stale_chapters, cleanup_stale_docx_jobs,
)
from .tasks import (
    start_workflow_task,
    resume_review_task,
    retry_failed_chapter_task,
    generate_docx_task,
)


class WorkerSettings:
    """⚠️ D-Z + D-AY:**所有任务 max_tries=1**(workflow 三类 + DOCX),通过
    @func(max_tries=...) 装饰器在 tasks.py 配置;
    ⭐ D-AJ:functions 与 cron_jobs 都直接放函数对象(不是字符串路径),
    避免依赖 arq 字符串导入路径下 wrapped attribute 是否被发现的隐式行为。"""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [
        start_workflow_task,
        resume_review_task,
        retry_failed_chapter_task,
        generate_docx_task,
    ]

    # ⭐ 给 DOCX 任务留并发余量(D-AA):workflow 上限是 max_concurrent_projects=10,
    # 但 DOCX 也共享这个 worker;若不留余量,10 个 DOCX 在等串行锁会占满 worker
    # job slot,新 workflow task 入队后排在 arq 队列里饿死。+2 余量(1 active DOCX + 1 备用)
    max_jobs = settings.max_concurrent_projects + 2
    job_timeout = 60 * 60 * 4                     # 单 job 上限 4 小时(全章节累计)
    keep_result = 86400
    on_startup = "bid_app.worker.lifecycle.on_startup"
    on_shutdown = "bid_app.worker.lifecycle.on_shutdown"

    # ⭐ cron(D-AG / D-AR / D-AS):全部用对象 import,与 functions 一致
    cron_jobs = [
        # 每分钟:清理 ACTIVE_SET 僵尸 + 同步 DB
        cron(reconcile_periodic,
             minute=set(range(0, 60)),
             unique=True, keep_result=0),
        # 每分钟:回滚卡中间态超 60s 的章节
        cron(cleanup_stale_chapters,
             minute=set(range(0, 60)),
             unique=True, keep_result=0),
        # 每 5 分钟:DOCX pending 超 30 分钟标 failed
        cron(cleanup_stale_docx_jobs,
             minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
             unique=True, keep_result=0),
    ]
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

@func(max_tries=1)
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
      # ⭐ D-DS:首启 init script 创建测试库 ${POSTGRES_DB}_test;
      # postgres 镜像在 /docker-entrypoint-initdb.d/ 下的 *.sql / *.sh
      # 仅在数据卷为空时执行(首次创建数据库时),无重复创建风险
      - ./docker/init-test-db.sh:/docker-entrypoint-initdb.d/10-init-test-db.sh:ro
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

### 17.5 测试库初始化脚本 `docker/init-test-db.sh`(D-DS)

```bash
#!/bin/bash
# ⭐ D-DS:postgres 容器首启时创建 ${POSTGRES_DB}_test。
# postgres 镜像在 /docker-entrypoint-initdb.d/ 下的 *.sh 仅在数据卷为空时执行;
# 已存在的数据库不会被覆盖,所以本脚本天然幂等(从环境变量看到的目标库名)。
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE "${POSTGRES_DB}_test";
    GRANT ALL PRIVILEGES ON DATABASE "${POSTGRES_DB}_test" TO "$POSTGRES_USER";
EOSQL
```

> 本地不用 docker 跑测试时,需先手动执行同等 SQL:
> ```bash
> psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
>   -c "CREATE DATABASE \"${POSTGRES_DB}_test\";"
> ```
> 也可以直接 `./scripts/create-test-db.sh` 封装这条命令(M1 验收清单可加)。
> `Base.metadata.create_all` 只能创建表,不能创建数据库 — `db_engine` fixture
> 在 `_test` 数据库不存在时会直接 connect refused,这是预期信号:实施者必须
> 先按上述方式建库。

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
    """⭐ D-DM + D-DO:每个测试(function scope,默认)一个全新的 schema。
    用 `settings.test_database_url`(独立测试库)而不是 `database_url`,
    启动校验 DB 名必须以 `_test` 结尾 — 防 teardown 的 `drop_all` 在
    `.env` 指错时把开发库表删光。

    若需 session-scope 共享 schema 加速(整套测试只 create/drop 一次),
    显式加 `scope="session"`,但要确保事务边界不污染数据(各测试用 SAVEPOINT
    回滚或 truncate)。当前 function scope 隔离最强,慢但不留状态。
    """
    # ⭐ D-DR:用 SQLAlchemy URL parser 解析数据库名,避免 query string / path
    # 末尾斜杠等特殊形态绕过简单字符串匹配
    from sqlalchemy.engine import make_url
    url = settings.test_database_url
    db_name = make_url(url).database or ""
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"refusing to drop_all on non-test DB (database={db_name!r});"
            " 修改 settings.test_database_url property 让数据库名以 '_test' 结尾,"
            " 或加 test_postgres_* 字段覆盖该 property"
        )
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(_use_test_session_factory):
    """⭐ D-DQ:依赖 `_use_test_session_factory` 而不是裸 `db_engine`。
    `_use_test_session_factory` 内部已 `app.dependency_overrides[get_db]`,
    HTTP 端点都走 test session;e2e 测试不会再绕开 monkeypatch。"""
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
├── test_docx.py
│   ├── test_docx_export_serialization_lock
│   ├── test_docx_export_with_mermaid
│   ├── test_docx_export_caches_after_first_run
│   # ⭐ D-CH:DOCX 状态机回归用例
│   ├── test_on_stage_rowcount_zero_raises_stale_and_skips_pandoc  # D-BX/D-CB
│   ├── test_get_docx_job_repairs_finalizing_when_file_exists      # D-CD
│   ├── test_get_docx_job_with_wrong_pk_returns_404                # D-CC
│   ├── test_commit_done_with_already_done_returns_ok              # D-CE
│   ├── test_assemble_invalidates_existing_done_docx               # D-CG
│   # ⭐ D-CS:D-CM 直接回归
│   ├── test_assemble_invalidates_inflight_jobs[pending|render|pandoc|finalizing]
│   ├── test_commit_done_skips_when_invalidated_and_unlinks_final  # D-CQ
│   # ⭐ D-CX / D-DA / D-DB / D-DF:D-CU 直接回归
│   ├── test_new_job_unlinks_stale_final_at_rendering              # D-CU 正常路径(D-DA 硬化)
│   ├── test_unlink_oserror_marks_job_failed_and_skips_render      # D-CU 失败路径(D-DB/D-DE)
│   └── test_unlink_happens_after_rendering_status_update          # D-DF 顺序不变量
└── test_worker_config.py             # ⭐ D-AJ 启动期断言
    ├── test_workflow_tasks_have_max_tries_1   # 三类 workflow task max_tries==1
    ├── test_docx_task_has_max_tries_1         # generate_docx_task max_tries==1(D-AY)
    └── test_worker_functions_are_decorated     # WorkerSettings.functions 都是
                                                # arq decorated 对象
```

```python
# tests/integration/test_worker_config.py
def test_workflow_tasks_have_max_tries_1():
    from bid_app.worker.tasks import (
        start_workflow_task, resume_review_task, retry_failed_chapter_task,
    )
    # arq @func 装饰器把 max_tries 挂在 .max_tries 或 .__arq_function__ 上,
    # 不同 arq 版本属性名可能差,这里用 hasattr + getattr 兼容
    for fn in (start_workflow_task, resume_review_task, retry_failed_chapter_task):
        mt = getattr(fn, "max_tries", None) or \
             getattr(getattr(fn, "__arq_function__", None), "max_tries", None)
        assert mt == 1, f"{fn.__name__} max_tries should be 1, got {mt}"


def test_docx_task_has_max_tries_1():
    """⭐ D-AY:DOCX 与其他 task 一致 max_tries=1"""
    from bid_app.worker.tasks import generate_docx_task
    mt = getattr(generate_docx_task, "max_tries", None) or \
         getattr(getattr(generate_docx_task, "__arq_function__", None), "max_tries", None)
    assert mt == 1, f"generate_docx_task max_tries should be 1, got {mt}"
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

### 18.5 DOCX 状态机回归测试汇总(D-CH / D-CL / D-CR / D-CS / D-CW / D-CX / D-CY / D-DA / D-DB / D-DD / D-DE / D-DF / D-DG / D-DH / D-DJ / D-DK / D-DL / D-DM / D-DN / D-DO / D-DP / D-DQ / D-DR / D-DS)

**conftest.py 必备 fixture**(D-CR:测试代码依赖,需在 conftest.py 落地):

```python
# tests/integration/conftest.py
import asyncio
import uuid

import pytest
import pytest_asyncio                          # ⭐ D-CY:与 §18.1 保持一致用 async fixture
from httpx import ASGITransport, AsyncClient   # ⭐ D-CW:httpx >= 0.28 用 ASGITransport

from datetime import datetime, timezone

from bid_app.core.security import hash_password
from bid_app.db import session_factory
from bid_app.deps import get_current_user
from bid_app.main import app
from bid_app.models import DocxJob, Project, Run, User


# ⭐ D-CY:统一用 @pytest_asyncio.fixture(而不是 @pytest.fixture async def)。
# pytest-asyncio strict 模式下 @pytest.fixture 不会按 async 处理,会让 fixture
# 整个失效;§18.1 已经这样写,这里保持一致最稳。也可以在 pyproject.toml 配
# `asyncio_mode = "auto"` 让 @pytest.fixture 自动识别,但不如显式装饰器明确

@pytest_asyncio.fixture
async def _use_test_session_factory(db_engine, monkeypatch):
    """⭐ D-DN:把所有已 import 的 `session_factory` 切到 test engine,
    并 override FastAPI 的 `get_db`。

    问题:`bid_app.db.session_factory` 是模块级单例,各模块在 import 时
    `from ..db import session_factory` 拿到的是同一对象;monkeypatch 后再 import
    OK,但已 import 的引用不会变。所以必须**逐个 monkeypatch**已知用 session_factory
    的模块。下方列出的几个是 v3.23 spec 里的全部出现点,新增模块要同步加。

    用 `expire_on_commit=False` 让 fixture/测试拿到的对象在 commit 之后还能访问
    属性(D-DH 已通过 flush+单 commit 缓解,但 fixture 之外的 task 内部代码也会
    多 commit,统一关 expire 最稳)。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from bid_app import db as bid_db
    from bid_app.deps import get_db
    from bid_app.main import app
    test_session_factory = async_sessionmaker(
        db_engine, expire_on_commit=False, class_=bid_db.session_factory.class_,
    )
    # ⭐ D-DQ:模块级单例替换 — 已 `from bid_app.db import session_factory`
    # 的模块都需要在这里加上。维护方法:
    #     rg --no-heading -t py "from .*db import session_factory" \
    #        backend/src/bid_app | awk -F: '{print $1}' | sort -u
    # 每次发现新模块用了 session_factory,把它加进 targets 列表。
    targets = [
        "bid_app.db",
        "bid_app.workflow.sync",
        "bid_app.workflow.nodes.assemble",
        "bid_app.workflow.nodes.write_chapter",   # D-DQ:_resolve_api_key 等
        "bid_app.worker.tasks",
        "bid_app.services.concurrency",
        "bid_app.services.llm",     # _llm_project_dir 用
        "bid_app.api.health",       # D-DQ:/health 端点 SELECT 1
    ]
    for mod in targets:
        monkeypatch.setattr(f"{mod}.session_factory", test_session_factory)

    async def _test_get_db():
        async with test_session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _test_get_db
    yield test_session_factory
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def db(_use_test_session_factory):
    """裸 session,测试中直接 INSERT/UPDATE/SELECT。

    ⭐ D-DJ + D-DN:依赖 `_use_test_session_factory`(它本身依赖 `db_engine`),
    保证 ① schema 已建 ② 应用所有路径走 test engine,不污染开发库。
    """
    async with _use_test_session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def user_factory(_use_test_session_factory):
    """⭐ D-CW:Project.created_by 是非空 FK,project_factory 不能写死 1;
    测试需要先建 User,project_factory 默认引用本 fixture 创建的 user.id。"""
    async def _make(**overrides):
        async with _use_test_session_factory() as s:
            u = User(
                username=overrides.pop("username", f"t_{uuid.uuid4().hex[:8]}"),
                password_hash=overrides.pop("password_hash", hash_password("x")),
                role=overrides.pop("role", "admin"),
                is_active=overrides.pop("is_active", True),
                must_change_password=overrides.pop("must_change_password", False),
                **overrides,
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u
    return _make


@pytest_asyncio.fixture
async def project_factory(tmp_path, user_factory, _use_test_session_factory):
    """创建 status='done' 的 Project + 真磁盘目录;created_by 引用真 User。

    ⭐ D-DD:默认 `create_done_run=True` 同时插一条 `Run(status='done')`。
    `generate_docx_task` 入口会 SELECT done Run 才往下跑,没 Run 直接 RuntimeError,
    走不到 D-CU/D-CX 的 unlink/finalize 测试逻辑。让 fixture 默认产出"DOCX 可生成
    所必需的最小项目状态"是最贴近真实 P6 入口前置的写法。"""
    async def _make(*, create_done_run: bool = True, **overrides):
        pdir = tmp_path / f"proj_{uuid.uuid4().hex[:8]}"
        pdir.mkdir()
        if "created_by" not in overrides:
            owner = await user_factory()
            overrides["created_by"] = owner.id
        # ⭐ D-DH:单事务 add → flush → 可选 add(Run) → 单次 commit → refresh。
        # 原写法是"commit → refresh → add(Run) → commit",第二次 commit 在
        # `expire_on_commit=True` 默认下会让 p 进 expired 状态,返回后访问
        # p.id / p.dir_path 触发 async lazy-load 在 detached session 上失败。
        async with _use_test_session_factory() as s:
            p = Project(
                name=overrides.pop("name", "t"),
                dir_path=str(pdir),
                status=overrides.pop("status", "done"),
                **overrides,
            )
            s.add(p)
            await s.flush()         # 拿 p.id,行未 commit
            if create_done_run:
                now = datetime.now(timezone.utc)
                s.add(Run(
                    project_id=p.id,
                    langgraph_thread_id=f"th_{uuid.uuid4().hex[:16]}",
                    started_at=now, finished_at=now,
                    status="done",
                ))
            await s.commit()
            await s.refresh(p)      # 一次 commit 后 refresh 拿全字段,无 expire 风险
            return p
    return _make


@pytest_asyncio.fixture
async def docx_job_factory(_use_test_session_factory):
    async def _make(*, project_id, status="pending", arq_job_id=None,
                    output_path=None, error=None):
        async with _use_test_session_factory() as s:
            j = DocxJob(project_id=project_id, status=status,
                        arq_job_id=arq_job_id,
                        output_path=output_path, error=error)
            s.add(j)
            await s.commit()
            await s.refresh(j)
            return j
    return _make


@pytest.fixture
def mock_redis_lock(monkeypatch):
    """⭐ D-DG:把 services/docx_export.py 的 `_redis_lock` 替换成 no-op
    asynccontextmanager;让直接调 `generate_docx_task` 的测试不需要真实 Redis。

    `export_docx` 内部 `async with _module_lock` 用的是同进程 asyncio.Lock,
    测试无需 fake;真正会卡测试的是 `_redis_lock` 内部 `redis.asyncio.from_url`。
    用例只要 `mock_redis_lock` 作为 fixture 参数即可启用。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _no_redis_lock(*a, **kw):
        yield
    monkeypatch.setattr(
        "bid_app.services.docx_export._redis_lock", _no_redis_lock,
    )


@pytest_asyncio.fixture
async def auth_client(user_factory, _use_test_session_factory):
    """⭐ D-CR + D-CW:override get_current_user 跳过鉴权;httpx >= 0.28
    要求用 ASGITransport(app=...) 而不是 AsyncClient(app=...)(后者已弃用)。"""
    fake_user = await user_factory()
    app.dependency_overrides[get_current_user] = lambda: fake_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
```

**回归用例**(`tests/integration/test_docx.py`):

```python
# tests/integration/test_docx.py — 完整覆盖
# D-CH / D-CL / D-CR / D-CS / D-CX / D-DA / D-DB / D-DD / D-DE / D-DF / D-DG / D-DH / D-DJ / D-DK
import asyncio                            # ⭐ D-CR:create_subprocess_exec 用得到
import uuid

import pytest
import sqlalchemy as sa
from pathlib import Path

# ⭐ D-DP:**不**在顶层 `from bid_app.db import session_factory`。
# 顶层 import 在 conftest fixture monkeypatch 之前求值,会让本模块名字
# `session_factory` 始终指向旧的全局对象,绕过 `_use_test_session_factory`。
# 所有需要原始 session 的测试用例改成依赖注入 `_use_test_session_factory` fixture,
# 直接调 `_use_test_session_factory()` 拿 test session。
from bid_app.models import DocxJob


@pytest.mark.asyncio
async def test_on_stage_rowcount_zero_raises_stale_and_skips_pandoc(
    db, project_factory, docx_job_factory, monkeypatch, tmp_path, mock_redis_lock,
    _use_test_session_factory,
):
    """⭐ D-BX + D-CB + D-CL:**测试触发点是 on_stage 切 pandoc 那一刻**,
    cleanup 在那个瞬间把 status 从 rendering_mermaid 抢标 failed → on_stage 抛
    _StaleJob → export_docx 透传 → task 顶层 catch,unlink tmp + 退出,
    pandoc 子进程从未被启动。
    **不能用初始 status=failed**:worker 入口 SELECT 看 done/failed 直接 return,
    根本走不到 on_stage(D-CL 修正 v3.15 测试逻辑错误)。
    """
    project = await project_factory()
    job = await docx_job_factory(project_id=project.id, status="pending")
    (Path(project.dir_path) / "proposal.md").write_text("# t", encoding="utf-8")

    pandoc_called = []
    real_create_subprocess = None
    async def spy_subprocess(*args, **kw):
        # 第一次调是 mermaid(mmdc),允许;之后调 pandoc 时 spy 并阻断
        if args and "pandoc" in str(args[0]):
            pandoc_called.append(args)
            class _P:  # 假装跑过的进程
                returncode = 0
                async def communicate(self): return (b"", b"")
            return _P()
        return await real_create_subprocess(*args, **kw)
    real_create_subprocess = asyncio.create_subprocess_exec
    monkeypatch.setattr(
        "bid_app.services.docx_export.asyncio.create_subprocess_exec",
        spy_subprocess,
    )

    # 关键:在 _update_stage("pandoc") 触发前注入 cleanup 抢标
    from bid_app.services import docx_export as dx
    orig_render = dx._render_mermaid
    async def render_then_steal_status(*args, **kw):
        result = await orig_render(*args, **kw)
        # mermaid 跑完 → 模拟 cleanup 把 status 直接改到 failed
        async with _use_test_session_factory() as s:
            await s.execute(sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='stolen by cleanup', updated_at=NOW() WHERE id=:i"
            ), {"i": job.id})
            await s.commit()
        return result
    monkeypatch.setattr(dx, "_render_mermaid", render_then_steal_status)

    from bid_app.worker.tasks import generate_docx_task
    result = await generate_docx_task(
        ctx={}, project_id=project.id, docx_job_id=job.id,
    )
    assert result == {"status": "stale", "output_path": None}
    assert pandoc_called == []     # pandoc 没被调用


@pytest.mark.asyncio
async def test_get_docx_job_repairs_finalizing_when_file_exists(
    auth_client, db, project_factory, docx_job_factory,
):
    """⭐ D-CD:GET 端点看到 finalizing + proposal.docx 存在 → inline 修成 done。"""
    project = await project_factory()
    (Path(project.dir_path) / "proposal.docx").write_bytes(b"fake docx")
    job = await docx_job_factory(project_id=project.id, status="finalizing")
    r = await auth_client.get(f"/api/projects/{project.id}/docx-job/{job.id}")
    assert r.status_code == 200
    body = r.json()
    # 公开映射:repair 后是 done(不再是 processing)
    assert body["status"] == "done"
    assert body["docx_job_id"] == job.id
    # DB 也应已落 done
    refreshed = (await db.execute(
        sa.text("SELECT status FROM docx_jobs WHERE id=:i"), {"i": job.id},
    )).scalar_one()
    assert refreshed == "done"


@pytest.mark.asyncio
async def test_get_docx_job_with_wrong_pk_returns_404(
    auth_client, project_factory, docx_job_factory,
):
    """⭐ D-CC:前端拿错 id 查(用 arq_job_id 那种字符串或不存在的 PK)→ 404,
    确保后端按 docx_jobs.id 索引而不是按 arq_job_id 兼容查询。"""
    project = await project_factory()
    job = await docx_job_factory(project_id=project.id, arq_job_id="arq:abc123")
    fake_pk = job.id + 99999
    r = await auth_client.get(f"/api/projects/{project.id}/docx-job/{fake_pk}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_commit_done_with_already_done_returns_ok(
    db, project_factory, docx_job_factory, _use_test_session_factory,
):
    """⭐ D-CE + D-CL:`_commit_done` 在 rowcount=0 但当前已 done 时静默成功。
    模拟 GET/cleanup repair 在 task `_commit_done` 之前抢先把 status 改 done。"""
    project = await project_factory()
    job = await docx_job_factory(project_id=project.id, status="finalizing")
    final_path = Path(project.dir_path) / "proposal.docx"
    final_path.write_bytes(b"x")
    # 模拟 D-BY/D-CD 抢先 repair
    async with _use_test_session_factory() as s:
        await s.execute(sa.text(
            "UPDATE docx_jobs SET status='done', output_path=:p "
            "WHERE id=:i"
        ), {"i": job.id, "p": str(final_path)})
        await s.commit()
    # 现在 task 跑到 _commit_done:WHERE status='finalizing' 命中 0 行,
    # SELECT 看到当前 done → 静默返回成功(无 stale)
    from bid_app.worker.tasks import _commit_done
    result = await _commit_done(job.id, final_path)
    assert result == {"output_path": str(final_path)}


@pytest.mark.asyncio
async def test_assemble_invalidates_existing_done_docx(
    db, project_factory, docx_job_factory,
):
    """⭐ D-CG + D-CM:assemble 重写 proposal.md 后,旧 DocxJob.done →
    invalidated,proposal.docx 文件被 unlink。"""
    project = await project_factory()
    docx_path = Path(project.dir_path) / "proposal.docx"
    docx_path.write_bytes(b"old docx")
    old_job = await docx_job_factory(project_id=project.id, status="done",
                                     output_path=str(docx_path))

    # ⭐ D-DK:用 project_factory 真实创建的 Run id,不写死 1
    run_id = (await db.execute(sa.text(
        "SELECT id FROM runs WHERE project_id=:p ORDER BY started_at DESC LIMIT 1"
    ), {"p": project.id})).scalar_one()

    # 直接调 assemble 节点的 run() 函数(state 仅含 assemble 实际用到的字段)
    from bid_app.workflow.nodes.assemble import run as assemble_run
    state = {
        "project_id": project.id,
        "run_id": run_id,
        "chapters": [], "finalized_chapters": [],
    }
    await assemble_run(state)

    refreshed = await db.get(DocxJob, old_job.id)
    assert refreshed.status == "invalidated"
    assert refreshed.output_path is None
    assert not docx_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("inflight_status",
                         ["pending", "rendering_mermaid", "pandoc", "finalizing"])
async def test_assemble_invalidates_inflight_jobs(
    db, project_factory, docx_job_factory, inflight_status,
):
    """⭐ D-CM:assemble 把 in-flight 任务也作废,不只 done。
    覆盖 D-CG → D-CM 的范围扩展;参数化跑 4 个 in-flight 状态。"""
    project = await project_factory()
    job = await docx_job_factory(project_id=project.id, status=inflight_status)

    # ⭐ D-DK:用真实 run_id
    run_id = (await db.execute(sa.text(
        "SELECT id FROM runs WHERE project_id=:p ORDER BY started_at DESC LIMIT 1"
    ), {"p": project.id})).scalar_one()

    from bid_app.workflow.nodes.assemble import run as assemble_run
    state = {
        "project_id": project.id,
        "run_id": run_id,
        "chapters": [], "finalized_chapters": [],
    }
    await assemble_run(state)

    refreshed = await db.get(DocxJob, job.id)
    assert refreshed.status == "invalidated"
    assert refreshed.output_path is None


@pytest.mark.asyncio
async def test_commit_done_skips_when_invalidated_and_unlinks_final(
    db, project_factory, docx_job_factory,
):
    """⭐ D-CM + D-CQ:finalizing 被 assemble 抢标 invalidated 后,_commit_done
    检查 rowcount=0 → SELECT 看到 invalidated → unlink final_path,**不**写 done。
    这是"finalizing/atomic rename 与 assemble 竞态"链路的最后一道防线。"""
    project = await project_factory()
    final_path = Path(project.dir_path) / "proposal.docx"
    final_path.write_bytes(b"already renamed")    # 模拟 task 已 rename
    job = await docx_job_factory(project_id=project.id, status="finalizing")
    # assemble 抢标 invalidated
    async with session_factory() as s:
        await s.execute(sa.text(
            "UPDATE docx_jobs SET status='invalidated', "
            "output_path=NULL, updated_at=NOW() WHERE id=:i"
        ), {"i": job.id})
        await s.commit()
    # task 跑 _commit_done:WHERE status='finalizing' 命中 0 行 → 看到 invalidated
    from bid_app.worker.tasks import _commit_done
    result = await _commit_done(job.id, final_path)
    assert result == {"status": "invalidated", "output_path": None}
    # 不变量验证(D-CV 弱版):best-effort cleanup 期望成功;
    # 失败时 D-CU 会在下次新 job rendering 阶段再清。本测试断 best-effort 成功路径
    #
    assert not final_path.exists()
    refreshed = await db.get(DocxJob, job.id)
    assert refreshed.status == "invalidated"


@pytest.mark.asyncio
async def test_new_job_unlinks_stale_final_at_rendering(
    db, project_factory, docx_job_factory, monkeypatch, tmp_path, mock_redis_lock,
):
    """⭐ D-CX(D-DA 修正版):验证 D-CU 正常路径 —
    旧 proposal.docx 残留时,新 job 切到 rendering_mermaid 后必须立即 unlink,
    且 unlink 必须早于 mmdc / pandoc 子进程启动。

    D-DA 修正:① proposal.md 写入真 mermaid block 触发 mmdc;② 完全 fake
    create_subprocess_exec 不依赖本机 mmdc/pandoc;③ 直接断顺序,不再 if 守护。
    """
    project = await project_factory()
    stale = Path(project.dir_path) / "proposal.docx"
    stale.write_bytes(b"old docx residue")
    job = await docx_job_factory(project_id=project.id, status="pending")
    # ⭐ D-DA:写 mermaid block 让 _render_mermaid 一定调 mmdc
    (Path(project.dir_path) / "proposal.md").write_text(
        "# t\n\n```mermaid\ngraph TD; A-->B\n```\n", encoding="utf-8"
    )

    call_log: list[str] = []

    # spy unlink:先保存原引用再 setattr,避免 __wrapped__ 不可靠
    real_unlink = Path.unlink
    def spy_unlink(self, *a, **kw):
        if str(self) == str(stale):
            call_log.append("unlink_stale")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", spy_unlink)

    # ⭐ D-DA:完全 fake create_subprocess_exec,**不**调用真实二进制。
    # 让 mmdc 假装"成功生成 PNG"(_render_mermaid 检查 png.exists() 后才信任成功);
    # 让 pandoc 假装"成功生成 docx"(_export_docx_inner 之后会 rename tmp 路径)。
    async def fake_subproc(*args, **kw):
        cmd = str(args[0]) if args else ""
        if "mmdc" in cmd:
            call_log.append("mmdc")
            # mmdc -i src -o png ... 第 4 个参数是 -o 后的 png 路径
            # 简化处理:扫 args 找 -o 之后的路径并 touch 一个文件
            arg_list = list(args)
            try:
                png_path = Path(arg_list[arg_list.index("-o") + 1])
                png_path.write_bytes(b"\x89PNG fake")
            except (ValueError, IndexError):
                pass
        elif "pandoc" in cmd:
            call_log.append("pandoc")
            arg_list = list(args)
            try:
                docx_path = Path(arg_list[arg_list.index("-o") + 1])
                docx_path.write_bytes(b"PK fake docx")
            except (ValueError, IndexError):
                pass
        class _P:
            returncode = 0
            async def communicate(self): return (b"", b"")
        return _P()
    monkeypatch.setattr(
        "bid_app.services.docx_export.asyncio.create_subprocess_exec",
        fake_subproc,
    )

    from bid_app.worker.tasks import generate_docx_task
    await generate_docx_task(ctx={}, project_id=project.id, docx_job_id=job.id)

    # ⭐ D-DA:直接断顺序,不再 if 守护
    assert "unlink_stale" in call_log, "D-CU 必须在 rendering 阶段 unlink 旧 final"
    assert "mmdc" in call_log, "fake mmdc 应被 _render_mermaid 触发"
    assert "pandoc" in call_log, "fake pandoc 应被 _export_docx_inner 触发"
    assert call_log.index("unlink_stale") < call_log.index("mmdc"), \
        "unlink 必须在 mermaid 启动之前(D-CU 顺序保证)"
    assert call_log.index("unlink_stale") < call_log.index("pandoc"), \
        "unlink 必须在 pandoc 启动之前"


@pytest.mark.asyncio
async def test_unlink_oserror_marks_job_failed_and_skips_render(
    db, project_factory, docx_job_factory, monkeypatch, mock_redis_lock,
):
    """⭐ D-CX:验证 D-CU 失败路径 — unlink 抛 OSError(权限/挂载只读),
    job 必须被标 failed,mermaid/pandoc 都不再启动。"""
    project = await project_factory()
    stale = Path(project.dir_path) / "proposal.docx"
    stale.write_bytes(b"residue")
    # ⭐ D-DE:写 proposal.md,跨过 §13.3 的 missing 检查走到 unlink 阶段
    (Path(project.dir_path) / "proposal.md").write_text("# t\n", encoding="utf-8")
    job = await docx_job_factory(project_id=project.id, status="pending")

    # ⭐ D-DB:先保存原 unlink 引用,再 monkeypatch — 不依赖 __wrapped__(后者
    # 在 monkeypatch.setattr 后并不一定存在;真实路径上对其它文件的 unlink 仍要正常)
    real_unlink = Path.unlink
    def boom_unlink(self, *a, **kw):
        if str(self) == str(stale):
            raise OSError("read-only filesystem")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", boom_unlink)

    rendering_calls: list[str] = []
    async def spy_subproc(*args, **kw):
        rendering_calls.append(str(args[0]) if args else "")
        class _P:
            returncode = 0
            async def communicate(self): return (b"", b"")
        return _P()
    monkeypatch.setattr(
        "bid_app.services.docx_export.asyncio.create_subprocess_exec", spy_subproc,
    )

    from bid_app.worker.tasks import generate_docx_task
    with pytest.raises(OSError):
        await generate_docx_task(ctx={}, project_id=project.id, docx_job_id=job.id)

    refreshed = await db.get(DocxJob, job.id)
    assert refreshed.status == "failed"
    assert "failed to clear stale final" in (refreshed.error or "")
    assert rendering_calls == []     # mermaid / pandoc 都没启动


@pytest.mark.asyncio
async def test_unlink_happens_after_rendering_status_update(
    db, project_factory, docx_job_factory, monkeypatch, mock_redis_lock,
    _use_test_session_factory,
):
    """⭐ D-DF:锁死 D-CU 顺序不变量 — UPDATE status='rendering_mermaid' 必须
    在 Path.unlink(stale_final_path) 之前执行。

    这是 D-CU 与 D-BX 链路的核心:状态前置(WHERE status='pending')保证只有
    当前 task 能切到 rendering;切换之后才能放心 unlink(否则 unlink 可能被
    pending 状态下竞态的多个 task 误触发,削弱守护)。
    """
    project = await project_factory()
    stale = Path(project.dir_path) / "proposal.docx"
    stale.write_bytes(b"residue")
    (Path(project.dir_path) / "proposal.md").write_text("# t\n", encoding="utf-8")
    job = await docx_job_factory(project_id=project.id, status="pending")

    call_log: list[str] = []

    # ⭐ spy session.execute,捕捉 UPDATE rendering_mermaid 那条 SQL。
    # ⭐ D-DP:从 _use_test_session_factory 拿"已切到 test engine"的 session 工厂,
    # 而**不是**重新 `from bid_app.db import session_factory`(那会拿到原始全局对象,
    # 绕过 D-DN 的 monkeypatch 让测试打到默认数据库)
    real_make_session = _use_test_session_factory

    class _SessionSpy:
        def __init__(self, inner):
            self._inner = inner
        async def __aenter__(self):
            self._sess = await self._inner.__aenter__()
            return self
        async def __aexit__(self, *a):
            return await self._inner.__aexit__(*a)
        async def execute(self, stmt, *a, **kw):
            sql_text = str(getattr(stmt, "text", stmt))
            if "rendering_mermaid" in sql_text and "UPDATE" in sql_text.upper():
                call_log.append("update_rendering_mermaid")
            return await self._sess.execute(stmt, *a, **kw)
        async def commit(self): return await self._sess.commit()
        def add(self, *a, **kw): return self._sess.add(*a, **kw)
        def __getattr__(self, name): return getattr(self._sess, name)

    def spy_session_factory(*a, **kw):
        return _SessionSpy(real_make_session(*a, **kw))

    monkeypatch.setattr(
        "bid_app.worker.tasks.session_factory", spy_session_factory,
    )

    real_unlink = Path.unlink
    def spy_unlink(self, *a, **kw):
        if str(self) == str(stale):
            call_log.append("unlink_stale")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", spy_unlink)

    async def fake_subproc(*args, **kw):
        cmd = str(args[0]) if args else ""
        arg_list = list(args)
        try:
            out_path = Path(arg_list[arg_list.index("-o") + 1])
            out_path.write_bytes(b"\x00")
        except (ValueError, IndexError):
            pass
        class _P:
            returncode = 0
            async def communicate(self): return (b"", b"")
        return _P()
    monkeypatch.setattr(
        "bid_app.services.docx_export.asyncio.create_subprocess_exec", fake_subproc,
    )

    from bid_app.worker.tasks import generate_docx_task
    await generate_docx_task(ctx={}, project_id=project.id, docx_job_id=job.id)

    assert "update_rendering_mermaid" in call_log
    assert "unlink_stale" in call_log
    assert call_log.index("update_rendering_mermaid") < call_log.index("unlink_stale"), \
        "D-CU 顺序不变量:UPDATE 状态切换必须在 unlink 之前"
```

> 上述 fixture(`docx_job_factory` / `project_factory`)在 `conftest.py` 实现:
> 工厂 fixture 直接 INSERT row 并 commit,绕过 API 走 DB,让用例聚焦在状态机分支
> 而非 API 链路。`run_assemble_node` 直接调 `assemble.run(state)`,不需要拉起整
> 个 LangGraph workflow。

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

> ⭐ **D-BE**:`services/llm.py` 主代码已经直接在 `call_llm_stream` 的重试 catch 块里调
> `_write_llm_error(...)`(见 §11.1),无需再单独整合 — 那是 v3.7 设计分裂的遗留,v3.9 已合并。
> `worker/tasks.py` 顶层 `except Exception` 仍保留 `append_error(...)` 调用,职责区分:
> LLM 服务层负责 `LLM retry` / `LLM exhausted`;worker 层负责 `task crashed` 整体堆栈。

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
pending ──(generate 开始)──→ generating ──(generate 完成)──→ awaiting_review     │
            ▲                                                       │            │
            │                                            (用户提交) ▼            │
            │                                                  reviewing ─┐      │
            │                                  (worker 接管,根据 decision)│      │
            │                                  ┌───── approve ────→ approved (terminal)
            │                                  ├───── skip ──────→ skipped  (terminal)
            │                                  └───── revise ────→ generating ──┘
            │                                                      (到 retry_count > max → 强制 skipped)
            │
            ├── (LLM 3 次失败 / 单章 10 分钟超时) ──→ failed
            │                                          │
            │                            (用户 retry) ▼
            │                                       retrying
            │                              (worker 接管,reset)
            └──────────────────────────────────── (status='pending' + retry_count=0)
                  ↑ 本轮 ChapterVersion 标 abandoned, last_error=NULL
```

> 中间态 reviewing / retrying(D-AI):API 在行锁内切入,worker 入口切出。这两个状态短暂(秒级),仅用于防双击/并发重复提交;前端看到这两个状态应当 disable 审核/重试按钮 + 显示"处理中"。
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
| R5 | arq 进程崩了 in-flight job 丢 | 全部 task `max_tries=1`(D-Z + D-AY) + LangGraph checkpoint 续跑(workflow)/ 用户手动重新生成(DOCX);keep_result=86400 | M1 |
| R6 | uvicorn 与 arq 同 .env 但分进程,settings 加载两次,密钥不一致 | gen-secrets 一次写到 .env,两进程读同一文件 | 启动后 logs 校验 |
| R7 | SSE 长连接被反向代理(未来加 nginx)切断 | 心跳每 20s `: ping`(已实现);nginx 加 `proxy_buffering off` 写进 README | 未来 |
| R8 | Postgres connection pool 被 LangGraph 与 SQLAlchemy 互相挤占 | LangGraph 用独立的 asyncpg pool;SQLAlchemy 池 size=10 | M1 测试 |
| R9 | 手作的 reference.docx 样式跑歪 | M3 第 1 天先空模板 + Pandoc 默认样式跑通,再手作 | M3 |
| R10 | 用户首次部署 BID_APP_MASTER_KEY 写错 → ApiKey 永久不可解密 | gen-secrets 强制生成 + 文档明确警告 + 启动校验长度 | 部署文档 |
| R11 | LangGraph state schema 改动后旧 checkpoint 不兼容 | 任何 state schema 变更 → 删 checkpoint 表(SQL 注释里写) | 升级文档 |
| R12 | DocxJob 卡任意 in-flight 状态(arq / chromium / pandoc 崩了)+ markdown 重生成时旧产物作废 | ⭐ 已实现(D-AS / D-AY / D-BH / D-BQ / D-BX / D-BY / D-CD / D-CE / D-CG / D-CJ / D-CM / D-CO / D-CQ / D-CU / D-CV):**核心不变量(D-CV 修订)**:**DB 是 source of truth**——`API 可下载 ⇔ latest DocxJob.status='done' AND proposal.docx 存在`。文件层面:`finalizing 期间 final_path 存在 ⇔ 当前 task 已 rename`(D-CU 强制保证,新 job 进 rendering 时 unlink 旧 final_path,unlink 失败则 task 标 failed)。invalidated 期间文件残留是允许的 best-effort 状态——API 层 D-CJ 不放行,下一次新 job 启动会被 D-CU 强制清理。完整链路:① cron `cleanup_stale_docx_jobs` 每 5 分钟扫超时 in-flight 标 failed;② finalizing **四处 repair**(cleanup / POST cached / GET /docx-job / GET 下载,D-BY / D-CJ POST / D-CD / D-CO),**所有 repair 的安全性都依赖 D-CU 的 rendering-stage unlink 前提**(否则旧文件残留会让 repair 把新 job 错标 done);③ task 阶段切换全部带 WHERE 状态前置 + rowcount 守护(D-BX);④ tmp + finalizing 抢占 + atomic rename + done rowcount 检查(D-BN/D-BQ/D-CE)保证写路径原子;⑤ assemble 重写 markdown 把 done + 全 in-flight DocxJob 标 invalidated(D-CG / D-CM),task 在 rename 前后两次校验 status(D-CQ);⑥ POST/GET API 都按 latest DocxJob 状态分流(D-CJ),不裸看文件存在 | M3 |

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
