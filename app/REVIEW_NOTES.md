# REVIEW_NOTES — code-reviewer 审查记录

本文件为 code-reviewer agent 的审查记录,按里程碑组织。
顶部 `PRE-REVIEW DRAFT` 段为 4 轮审查的 checklist 草案(基于 IMPLEMENTATION_SPEC.md v3.28 §3.1 决策表 + §22 + §23 整理),实际审查时按此清单逐条核查代码。

> 严重 bug 通过 SendMessage 发回对应 agent;非阻塞建议留在本文件 "review notes" 段。

---

## PRE-REVIEW DRAFT — Checklists (起草于审查启动前,审查时按此核查)

### 共通审查原则

- 严重 bug 限定:违反 D-* 不变量 / 反 spec / 安全漏洞 / 数据损坏路径 / 死锁;**非阻塞 nitpick 不发**。
- SendMessage 严重 bug 时必须含:文件:行号 + spec 引用(§X 或 D-XX)+ 修复建议。
- D-* 决策表里很多决策被后续决策"⚠️ 取代/收紧",以**最新决策为准**。审查时遇到"看起来不对"的实现,先确认是否被新决策推翻。
- LangGraph state ↔ DB 同步走 `workflow/sync.py`,白名单字段(D-BP):`status / final_text / last_error / retry_count / processing_started_at`。caller 写非白名单字段必须 raise。

---

### REVIEW-1:M0 + M1 后端(任务 #35,blocked by #1 #5 #11 #13)

覆盖范围:`pyproject.toml` / `config.py` / `services/llm.py` / `workflow/{state,prompts,nodes,graph,sync}.py` / `services/document_extractor.py` / `services/docx_export.py`(smoke) / `cli/{test_llm,run_local}.py` / `models/` / `migrations/0001_initial.py` / `db.py` / `deps.py`(最小版) / `events/bus.py` / `services/concurrency.py` / `worker/{settings,lifecycle,tasks}.py` / `api/{projects,chapters,stream}.py`。

#### 数据模型(§8 / D-C / D-S / D-BU / D-BQ / D-CG)

- [ ] 10 张表全:User / ApiKey / Project / Document / Run / Chapter / ChapterVersion / ReviewEvent / TokenUsage / DocxJob,且 `models/__init__.py` 全部 export(spec §8 末尾的 `__all__`)。
- [ ] **`Project.encrypted_api_key_snapshot: bytes | None`**(D-C);**`Project.api_key_owner` ON DELETE RESTRICT**;`status` 默认 `init`;状态枚举包含 `queued`(D-P)。
- [ ] **`Chapter.processing_started_at`**(D-AR + D-BF);`status` 枚举有 `reviewing / retrying / generating`(D-AI / D-BF)。
- [ ] **`ChapterVersion.abandoned: bool default false`**(FR-4.7)。
- [ ] **`ReviewEvent.aborted: bool default false NOT NULL`**(D-BI)。
- [ ] **`TokenUsage.project_id` ON DELETE CASCADE**(FR-1.6,v2 SET NULL 是错的);`run_id` ON DELETE SET NULL。
- [ ] **`DocxJob.arq_job_id: str | None`**(D-S);`status` 枚举:`pending / rendering_mermaid / pandoc / finalizing / done / failed / invalidated`(D-BQ + D-CG);`updated_at` 字段 `server_default + onupdate`(D-BH)。

#### Migration 0001(§9)

- [ ] partial unique index:`uq_docx_jobs_arq_job_id` (arq_job_id) WHERE arq_job_id IS NOT NULL(D-S)。
- [ ] partial unique index:`uq_docx_jobs_project_inflight` (project_id) WHERE status IN ('pending','rendering_mermaid','pandoc','**finalizing**')(D-S + D-BQ;不含 `invalidated`)。
- [ ] **chapters partial 索引 `ix_chapters_processing`** 范围:`status IN ('reviewing','retrying','generating') OR (status='pending' AND processing_started_at IS NOT NULL)`(D-BZ)。
- [ ] `token_usage.project_id` ON DELETE CASCADE(D-BU 数据模型)。
- [ ] `Project.status` 枚举含 `queued`(D-P);初始 admin 只在 0001 写入(默认 admin/admin123,must_change_password=true)。

#### LangGraph State / Graph(§10.1 / §10.2 / D-K)

- [ ] **5 个 Loop 变量名严格**:`current_index`、`retry_count`、`finalized_chapters`、`revision_feedback`、`chapters`(spec §10.1 / §10.4 / D-AY 不变):任何重命名都会跨节点断链,严重 bug。
- [ ] state.py **不放 `api_key`** 字段(D-C);否则会被 PostgresSaver 落库,污染 checkpoint。
- [ ] graph.py 节点齐全:`extract_documents / generate_outline / parse_outline / outline_review / pick_chapter / write_chapter / gen_visuals / merge_chapter / human_review / update_state / assemble`,且 `outline_review`(D-K 新 interrupt 节点)在 parse_outline 与 pick_chapter 之间。
- [ ] `update_state` conditional edge:`current_index < len(chapters)` → pick_chapter,否则 → assemble(§10.2)。

#### update_state 节点(§10.4 / FR-4.2 收紧)

- [ ] `decision == "approve"` / `"skip"`:`current_index += 1`、`retry_count = 0`、`revision_feedback = ""`,append 到 finalized_chapters。
- [ ] `decision == "revise"`:`new_retry = retry_count + 1`;**判定用 `>` 而不是 `>=`**(FR-4.2 / spec §10.4 注释);超限 → 强制 skip 并保留 pending 文本。
- [ ] revise 分支:`current_index` 不变(spec 头部强调"只有 Pass/Skip 才 +1")。
- [ ] sync_chapter_to_db 调用字段不超出 D-BP 白名单。

#### outline_review / human_review / assemble 节点

- [ ] `outline_review`:落 chapters 到 DB → set Project.status='outline_ready' → publish event → `interrupt(...)` → resume 后切回 'running' + 落编辑后 chapters(replace=True 时先 DELETE)。
- [ ] `human_review`:set Project.status='awaiting_review' → publish event → `interrupt(...)` → resume 后切 'running',注入 `_review_decision / _review_feedback`。
- [ ] `assemble`:写 `{project_dir}/proposal.md` → set Run.status='done' → set Project.status='done' → publish 'proposal_ready' → **同步作废 DOCX**(D-CG + D-CM):unlink proposal.docx + UPDATE docx_jobs SET status='invalidated' WHERE project_id=:p AND status IN ('done','pending','rendering_mermaid','pandoc','finalizing')。

#### LLM 服务(§11 / D-D / D-AU / D-BE / D-BG / D-BO)

- [ ] `call_llm_stream`:`async with asyncio.timeout(SINGLE_CHAPTER_TIMEOUT_SECONDS)` **包住整个流式收集**(不是 `await wait_for`);外层 `try/except TimeoutError` 写 `LLM total timeout` errors.log → raise `LLMTimeoutExceeded`(D-D + D-BG)。
- [ ] **每次重试都调 `_write_llm_error(... attempt=...)`** 写 errors.log;重试用尽 → 写 `LLM exhausted`(D-BE)。
- [ ] `call_llm_json`:JSON parse 失败 → `_write_llm_error` + `LLMRetryFailed`(D-BO);外层 `asyncio.timeout(120)` + TimeoutError catch(D-BG)。
- [ ] `ChapterGenerationFailed` 异常:由 `write_chapter` 节点(LLM-2)在 `LLMRetryFailed / LLMTimeoutExceeded / asyncio.TimeoutError` 后包一层 raise,带 `chapter_index / chapter_id`(D-AU)。
- [ ] `write_chapter`:取 api_key 走 `Project.encrypted_api_key_snapshot` → `decrypt_api_key`(D-C);**不**反查 ApiKey 表。
- [ ] `write_chapter`:切 generating 时同时写 `processing_started_at=NOW()`(D-BF + D-BK)。

#### Workflow sync.py(§10.6 / D-BP)

- [ ] `_CHAPTER_SYNC_ALLOWED = {status, final_text, last_error, retry_count, processing_started_at}`;非白名单或非 isidentifier → raise ValueError(D-BP)。
- [ ] `publish_event` 异常吞掉只 log,不传播(EventBus 失败不影响数据正确性,D-J)。

#### EventBus + SSE(§12 / D-A / D-B)

- [ ] `events/bus.py`:Redis pub/sub 跨进程(D-B);`subscribe` 用 asynccontextmanager。
- [ ] `api/stream.py`:首条 `event: ready` 立即推;**每 ≤ 20s 一次心跳 `: ping`**(asyncio.wait_for 超时分支或独立循环);`X-Accel-Buffering: no` 头(防 nginx buffer)。
- [ ] SSE token 流由 `_do_stream` 的 `async for chunk` 内 `event_bus.publish(... chapter_token, delta)` 触发(§11)。
- [ ] `chapter_failed` 事件由 `write_chapter` 在 LLM 失败时 publish(spec §11.2)。

#### Concurrency / Slot(§10.7 / D-T / D-Y / D-AB / D-AF / D-AN / D-AQ)

- [ ] `try_acquire_project_slot` 三态:`AcquireResult(token, reason)`,reason ∈ {ok, full, already_active, stale_evicted};Lua 返回 1/0/-1/-2。
- [ ] **RESERVE_TTL=300 / ALIVE_TTL=60 / HEARTBEAT_INTERVAL=20**(D-Y)。
- [ ] `ensure_project_slot`:简单 GET 比对 token;`heartbeat_project`:**Lua CAS**(GET + SET EX)。
- [ ] `release_project_slot(... token=...)`:Lua CAS,仅当持有 token 才 DEL + SREM(D-AB)。
- [ ] `_evict_stale_project`:SREM + DB UPDATE projects SET status='failed' WHERE status IN ('running','extracting','outlining')(D-AN 绑定原则)。
- [ ] `cleanup_stale_chapters` cron:**用 `get_alive_project_ids()` 排除**(SET ∩ ALIVE_KEY,D-BB),不是裸 SMEMBERS;SQL 用 `CAST(:active_ids AS int[])`;三段超时(reviewing/retrying 60s、pending 60s NOT NULL 守护、generating 15min)+ pending 必须 `processing_started_at IS NOT NULL`(D-BS / D-BL)。
- [ ] `cleanup_stale_chapters`:对 generating/pending 回滚的 project,UPDATE projects SET status='awaiting_review' WHERE status IN ('running','extracting','outlining','failed')(D-BL / D-BS)。
- [ ] `wake_queued_projects`:WAKE_LOCK SETNX EX 30;**异常 queued 项目立即标 failed**(already_active / stale_evicted / run 缺失,D-AX);enqueue 失败 → release + 标回 queued。
- [ ] `reconcile_active_projects`:worker startup 跑;清完返回 zombies 列表;调用方标 failed。

#### Worker tasks(§10.7 末尾 / D-Z / D-I / D-AB / D-AH / D-AT / D-AU / D-AW / D-AZ)

- [ ] **三类 task `@func(max_tries=1)`**:`start_workflow_task` / `resume_review_task` / `retry_failed_chapter_task`(D-Z + D-AY)。
- [ ] 每个 task **token 拿到后立即进 try/finally**(D-AH);finally 块 release_project_slot + wake_queued_projects。
- [ ] `start_workflow_task`:try 内 `_set_project_status('running')` → `build_initial_state` → `graph.astream(initial, config)`;每步 `if lost_event.is_set() or not await ensure_project_slot(...): raise SlotLost`(D-AM)。
- [ ] `resume_review_task`:**worker 入口写 ReviewEvent**(D-AC),`s.flush()` 拿 `review_event_id`(D-BT);`decision='revise'` 时 `chapter status='reviewing' → 'generating'` + `processing_started_at=NOW()`(D-AZ + D-BK);approve/skip **不切 generating**(D-AZ)。
- [ ] `retry_failed_chapter_task`:写 ReviewEvent(decision='retry_failed') + `abandoned=true` 历史版本 + `chapter status='retrying' → 'pending'` + `retry_count=0` + `last_error=NULL` + `processing_started_at=NOW()`(D-BS);`graph.aupdate_state(retry_count=0)` + `astream(None)`(D-I)。
- [ ] `SlotLost` 分支调 `_slot_lost_compensation(... action, decision, review_event_id)`,按 decision 分流回滚(approve/skip → awaiting_review,其它 → failed,D-AZ);标 ReviewEvent aborted **仅当 chapter 真被回滚**(D-BM);精确按 review_event_id 而不是按 chapter_id 取最近一条(D-BT)。
- [ ] `ChapterGenerationFailed` 分支:`_set_project_status('awaiting_review')`;**不写 errors.log + 不 raise**(D-AU)。
- [ ] generic `Exception` 顶层:`append_error` 写完整 traceback → `_fail_project_and_run`(D-BA + D-X + D-AE)同时标 Project + Run failed → raise。

#### Worker settings / lifecycle(§17.2 / D-AA / D-AJ)

- [ ] `WorkerSettings.functions = [start_workflow_task, resume_review_task, retry_failed_chapter_task, generate_docx_task]`(对象,不是字符串路径,D-AJ)。
- [ ] `max_jobs = max_concurrent_projects + 2`(D-AA)。
- [ ] `cron_jobs` 三个:`reconcile_periodic`(每分钟)、`cleanup_stale_chapters`(每分钟)、`cleanup_stale_docx_jobs`(每 5 分钟,D-AS / D-AY)。
- [ ] `on_startup`:setup checkpointer + reconcile_active_projects + wake_queued_projects(§10.7 末尾)。

#### deps.py 最小版(D-EC,M1 Day1)

- [ ] **只**实现 `get_db` + `get_current_user` dev/test stub(读 `BID_APP_DEV_USER_ID` 或回退查 users 表第一个 admin);查不到抛 500。
- [ ] **不**实现 JWT cookie / `must_change_password 428` / `get_current_user_lax` / `require_admin`(归 M2)。
- [ ] M1 测试通过 `app.dependency_overrides[get_current_user]` 注入 fake user。

#### API 端点 — projects(§15.1 / D-C / D-AF / D-T / D-U)

- [ ] `POST /start`:校验 `project.status == 'init'`(D-AF) → 拷贝 `ApiKey.encrypted_key` 到 `Project.encrypted_api_key_snapshot`(D-C 真快照) → 单次 `try_acquire_project_slot`(D-T) → `result.acquired ? extracting : queued` → enqueue start_workflow_task(传 `slot_token`) → enqueue 失败补偿 release + 改回 init + Run aborted(D-U)。
- [ ] `PUT /outline`(`/confirm-outline`,D-K):状态校验 `outline_ready` → `try_acquire` → enqueue `resume_review_task` payload `{kind: outline_confirm, chapters}`;503 时 `Retry-After: 60`。
- [ ] `POST /documents`:**日配额 SUM(file_size)** 校验 500MB(NFR-4 / 修复 v2 缺失)。
- [ ] `DELETE /{project_id}`:DB delete + 磁盘 rmtree(失败只 log,FR-1.6);CASCADE 已带删 token_usage。
- [ ] `GET /proposal` `/proposal.md` 返回 final_proposal / proposal.md。

#### API 端点 — chapters(§15.2 / D-AD / D-AI / D-AO / D-AR)

- [ ] `POST /chapters/{idx}/review`:行锁 + status 校验 `awaiting_review` → 切 `reviewing` + `processing_started_at=NOW()` → `try_acquire`(失败 503 Retry-After:60) → enqueue resume_review_task(传 reviewer_id / chapter_id);**ReviewEvent 由 worker 入口写**(D-AC,API 不写)。
- [ ] **补偿全包**(D-AO):任何异常(含 Redis / SQLAlchemy 等)都 release_slot + 章节回 awaiting_review + processing_started_at=NULL。
- [ ] `POST /chapters/{idx}/retry`:类似,中间态 `retrying`;补偿回 `failed`。

#### API 端点 — stream(§12.3)

- [ ] `GET /{project_id}/stream`:Depends get_current_user;立即 `event: ready`;heartbeat 兼容(asyncio.wait_for + ping)。

#### CLI / smoke 工具

- [ ] `cli/test_llm.py`:三模型各打一次 ≥ 100 chars;支持 `--api-key` 参数 + `BID_APP_FAKE_LLM=1` 短路。
- [ ] `cli/run_local.py`:命令行交互式审核 → 完整 markdown 产出 ≥ 5000 字。
- [ ] `services/document_extractor.py`:markitdown 包装。
- [ ] `services/docx_export.py`(M0 smoke 版):mermaid 块直接保留 fence(不调 mmdc)、不挂 reference.docx;pandoc 直转。
- [ ] D-DZ / D-ED 收窄:M0 验收"`pandoc proposal.md -o smoke.docx` 不报错 + Word 能打开,样式不要求"。

---

### REVIEW-2:M2 + M3 后端(任务 #36,blocked by #18 #22 #23)

覆盖范围:`core/{security,crypto,rate_limit,security_headers,login_throttle}.py` / `deps.py`(完整版) / `api/{auth,me,admin}.py` / `services/api_key_validator.py` / `services/docx_export.py`(完整版) / `worker/tasks.py:generate_docx_task` / `api/docx.py` / `templates/reference.docx` / mermaid 中文配置。

#### Auth & Crypto(§14.1 / §14.2)

- [ ] `core/crypto.py`:**AES-GCM + 12B nonce**;encrypt 输出 `nonce + ciphertext`(bytes);decrypt 拆 `[:12] + [12:]`;`_KEY = bytes.fromhex(settings.bid_app_master_key)`(64 hex)。
- [ ] `core/security.py`:bcrypt rounds=12;ACCESS_TTL=2h / REFRESH_TTL=7d;`decode_token` 校验 `kind` 字段。
- [ ] **DB 直接看 `ApiKey.encrypted_key` 是 bytes 不是明文**;`Project.encrypted_api_key_snapshot` 同样是密文。
- [ ] master key 启动校验(spec §6 / §7 表述);格式不对 raise。

#### Login throttle + Global rate limit(§14.3 / D-Q / NFR-4)

- [ ] `core/login_throttle.py`:`record_fail` INCR `bid_app:login_fail:{ip}` + 首次 EXPIRE 60s + 达 `login_fail_max_per_minute=5` SET `bid_app:login_lock:{ip}` EX 300;`is_locked` GET lock key;`clear_fails` DEL fail key(锁不动)。
- [ ] `api/auth.py login`:0. is_locked → 429;1. 用户/密码错 → record_fail(返回是否被锁)→ 429 / 401;2. 成功 → clear_fails + JWT cookie(`httponly + samesite=strict + max_age`);3. is_active=false → 403。
- [ ] `core/rate_limit.py`:slowapi `Limiter(default_limits=[settings.global_rate_limit])`,key_func=get_remote_address,storage_uri=Redis。
- [ ] `main.py`:`app.add_middleware(SlowAPIMiddleware)`(否则 default_limits 不生效)+ `add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)`。

#### Security headers(§14.4)

- [ ] `SecurityHeadersMiddleware`:`X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer` / **CSP**(default-src 'self';img-src self data: blob:;style-src self 'unsafe-inline';script-src 'self';frame-ancestors 'none')。
- [ ] `main.py` 注册顺序:SlowAPIMiddleware → SecurityHeadersMiddleware → TraceIdMiddleware。

#### deps.py 完整版(§14.5 / D-EC / D-F)

- [ ] `get_current_user` 严格版:JWT cookie → decode_token('access') → User → must_change_password=true 抛 **428 PRECONDITION_REQUIRED + detail={"error": "must_change_password"}**(D-F)。
- [ ] `get_current_user_lax` 宽松版:不检查 must_change_password(仅 /api/auth/me、/api/me/change-password、/api/auth/logout 用)。
- [ ] `require_admin`:role!='admin' 抛 403。
- [ ] **删除 `BID_APP_DEV_USER_ID` 分支**(M2 起走真登录)。

#### API auth / me / admin(§14.6 / §15.5)

- [ ] `/api/auth/login` `/logout` `/me`(参考 §14.6)。
- [ ] `/api/me/change-password` 用 lax(改密前自己挂着 must_change=true);改完写 `must_change_password=false`。
- [ ] `/api/me/api-key` PUT:先 `validate_dashscope` 再 encrypt_api_key 写 DB;upsert 走 `(user_id, provider)` 唯一约束。
- [ ] `/api/me/api-key/test`(D-G):用已存的 Key 调一次 dashscope 最小请求;成功更新 `last_validated_at`。
- [ ] `/api/admin/*`:`Depends(require_admin)`;用户 CRUD + 全局 token 消费汇总。
- [ ] FR-6.6:7 个豁免端点(/login /logout /me /change-password /api-key /api-key/test 之类)能在 must_change=true 时通,其它端点全 428。

#### DocxJob 状态机(§13.1 / §13.3 / §15.3 / D-BN / D-BX / D-BQ / D-BY / D-CD / D-CO / D-CG / D-CM / D-CU / D-CV / D-CE / D-CQ)

##### `services/docx_export.py`(完整版)

- [ ] `_module_lock` `await asyncio.wait_for(.acquire(), timeout=120)`(D-BR);超时 raise TimeoutError。
- [ ] `_redis_lock` 用 `r.lock(... timeout=300, blocking=True, blocking_timeout=120, thread_local=False)`;`acquired=False` raise TimeoutError。
- [ ] `MERMAID_RE`:容忍 ``` / ~~~ 围栏 + 行尾空格 + CRLF(D-N);`re.finditer` + 反向 span 替换。
- [ ] mmdc 命令:`-c /etc/mermaid-config.json -p /etc/puppeteer-config.json --cssFile /etc/mermaid.css`;失败 fence 保留(降级)。
- [ ] **写到 `proposal.{job_id}.tmp.docx`**(D-BN),不是 proposal.docx;调用方决定 atomic rename。
- [ ] `on_stage("pandoc")` 在 mermaid 完成后、pandoc 启动前调;**不再 try/except 兜底**(D-CB);`_StaleJob` 必须透传到 task 顶层。
- [ ] reference.docx 存在才 `--reference-doc=`;pandoc `--resource-path=work` 解析相对图片路径。

##### `worker/tasks.py:generate_docx_task`(§13.3)

- [ ] `@func(max_tries=1)`(D-AY,与 D-Z 一致)。
- [ ] **入口校验** SELECT status:不存在 → log error + return;done/failed → log + return;**invalidated → 直接 return**(D-CM)。
- [ ] **`pending → rendering_mermaid` UPDATE WHERE status='pending'** + rowcount=0 → return stale(D-BX)。
- [ ] **进 rendering_mermaid 后立即 `final_path.unlink(missing_ok=True)`**(D-CU);OSError → UPDATE failed + raise(D-CU 强制不变量)。
- [ ] `_update_stage("pandoc")`:`UPDATE WHERE status='rendering_mermaid'` + rowcount=0 → raise `_StaleJob`(D-BX)。
- [ ] export_docx 异常 → `UPDATE failed WHERE status IN ('pending','rendering_mermaid','pandoc','finalizing')`(D-BH + D-BQ)。
- [ ] **抢 finalizing**:`UPDATE status='finalizing' WHERE status IN ('pending','rendering_mermaid','pandoc')` + rowcount 守护(D-BQ);失败 → unlink tmp + return stale。
- [ ] **rename 之前再查一次 status**:`invalidated` → unlink tmp + return invalidated(D-CQ)。
- [ ] **rename 在 done 之前**:`tmp_path.rename(final_path)` → 失败 UPDATE failed + unlink tmp + raise(D-BN + D-BQ)。
- [ ] `_commit_done`:`UPDATE done WHERE status='finalizing'` + rowcount 守护;rowcount==0 时 SELECT 当前状态分类:done(D-BY/D-CD 抢先 repair)→ 静默;invalidated(D-CQ)→ best-effort unlink final + return invalidated;其它 → log warning + return stale(D-CE)。

##### `cleanup_stale_docx_jobs` cron(§10.7)

- [ ] **覆盖所有 in-flight**:`pending / rendering_mermaid / pandoc / finalizing`(D-AY + D-BQ);`updated_at < NOW() - 30min`(D-BH)。
- [ ] **finalizing repair pass**:扫 finalizing 行,文件存在 → `UPDATE done`(D-BY);文件不存在 + updated_at 超时 → 标 failed。
- [ ] 标 `invalidated` 不算 in-flight,不被本 cron 动(D-CG)。

##### `api/docx.py`(§15.3 / D-CC / D-CJ / D-CK / D-CO / D-CD)

- [ ] `POST /proposal.docx`:① 命中缓存前 finalizing repair pass(D-BY 在 POST 入口);② **看 latest DocxJob**:`cached.exists() AND latest.status='done'` 才 cached=true 并返回 `latest.id`(D-CK);否则走 INSERT pending(latest=invalidated 也走新建,D-CJ)。
- [ ] `POST` 顺序:**先 `db.commit()` 让行可见,再 enqueue,最后回写 arq_job_id**(D-AK);enqueue 失败 → UPDATE failed + 503;enqueue 返回 None → 同上;返回 `{docx_job_id, arq_job_id, cached}`(D-CC)。
- [ ] `GET /docx-job/{docx_job_id}`(D-BW):**inline finalizing repair**(D-CD)→ 状态映射 `pending/rendering_mermaid/pandoc/finalizing → "processing"`(D-CN);invalidated 直传;含 stage 中文文案。
- [ ] `GET /proposal.docx` 下载:**inline finalizing repair**(D-CO)→ 看 latest:invalidated → 409 `{code: "docx_invalidated"}`;非 done → 409 `{code: "docx_not_ready"}`;done 但文件不存在 → UPDATE failed + 409 `{code: "docx_missing"}`;done + 文件 → FileResponse + Content-Disposition `filename*=UTF-8''<encoded>`(FR-5.6 中文文件名,fallback ASCII)。

#### Mermaid 中文字体配置(§13.2)

- [ ] `docker/mermaid-config.json`:`fontFamily: "Noto Sans CJK SC, sans-serif"`。
- [ ] `docker/puppeteer-config.json`:`--no-sandbox`、`executablePath: /usr/bin/chromium`、`headless: "new"`。
- [ ] `docker/mermaid.css`:`* { font-family: "Noto Sans CJK SC", sans-serif !important; }`。
- [ ] `templates/reference.docx`:存在(占位即可,M3 Day1 用 LibreOffice 手作)。

---

### REVIEW-3:M4 前端(任务 #34,blocked by #27)

覆盖范围:`frontend/` 全部 vite + tailwind + shadcn 项目;路由 / RequireAuth / 8 个页面 / SSE hook / react-markdown + mermaid。

#### 项目初始化(§16.6)

- [ ] vite + react + tailwind + shadcn;`tsconfig` 路径别名 `@`;`vite.config.ts` proxy `/api` 与 `/health` → `http://127.0.0.1:12123`。
- [ ] pnpm 锁文件存在;无 console.error 在 dev / build。

#### 路由 + RequireAuth(§16.1)

- [ ] `RequireAuth` 检查 `must_change_password`:true 时跳 `/change-password`(除非组件加 `allowMustChange`);role=admin 才能进 /admin。
- [ ] 路径全:/login、/change-password、/、/projects/new、/projects/:id/upload、/projects/:id/outline、/projects/:id/review、/projects/:id/proposal、/settings、/admin。

#### apiFetch 拦截器(§16.2)

- [ ] **401 → 跳 /login;428 → 跳 /change-password**(对应 D-F)。
- [ ] `credentials: 'include'`;204 处理;ApiError 含 status + body。

#### 8 个页面 console error 路径(D-E)

- [ ] LoginPage / ChangePasswordPage:错误提示用户友好;clear feedback。
- [ ] SettingsPage:API Key 配置 + 测试连通(`GET /api-key/test`)+ token 消费图表;DashScope banner D3 一次性提示。
- [ ] ProjectListPage:团队共享池;无项目时友好空态。
- [ ] NewProjectPage / DocumentUploadPage:.docx/.doc/.md/.txt 限制;> 500MB 友好 413 提示。
- [ ] OutlineConfirmPage:可选编辑(P4)→ PUT `/outline` body.chapters。
- [ ] **ChapterReviewPage**(M4 最难):
  - [ ] `useProjectStream` SSE hook:`EventSource(... withCredentials)`;`useEffect` cleanup `es.close()`(无 race / 内存泄漏)。
  - [ ] **流式 token**:`chapter_token` 事件按 chapter_index 累积到 `streamingText`;`chapter_ready` 重置;`awaiting_review` 提示用户审核;**`chapter_failed` 显示重试按钮**(红标)。
  - [ ] **react-markdown + remarkGfm + Mermaid**:`code language-mermaid` 块用客户端 `mermaid.render` 自渲(§16.4);中文不乱码(`securityLevel: 'loose'`)。
  - [ ] ReviewActions 三按钮 pass/revise/skip;revise 必须 feedback 文本;disabled 状态正确(reviewing/retrying/generating 时不可点)。
  - [ ] ChapterVersion 历史 tab:过滤 `abandoned=false`(FR-4.7);展示 decision / feedback。
- [ ] ProposalPage:全文预览 + 复制 + .md / .docx 下载 + 进度条(轮询 `/docx-job/{id}` 看 stage);`docx_invalidated` 时展示"原文档已更新,重新生成"按钮(D-CG)。
- [ ] AdminPage:用户增删 + token 消费汇总。

#### shadcn 风格

- [ ] 按钮 / 表单 / 表格统一风格;无明显 unstyled 元素。

---

### REVIEW-4:M5 部署 + 整体集成(任务 #33,blocked by #31)

覆盖范围:`Dockerfile` / `docker/{entrypoint.sh,supervisord.conf,init-test-db.sh,pg-backup.sh,mermaid*}` / `docker-compose.yml` / `docker-compose.dev.yml` / `scripts/{install,gen-secrets,restore-backup,create-test-db,pg-backup}.sh` / `.env.example` / `README.md`;最后写 `app/ACCEPTANCE_AUDIT.md` 走查 §23 23 个 checkbox。

#### Dockerfile(§17.1)

- [ ] **依赖装齐**:pandoc / chromium / fonts-noto-cjk + extra / nodejs+npm / supervisor / cron / **postgresql-client-16**(从 pgdg APT)/ tzdata。
- [ ] `npm install -g @mermaid-js/mermaid-cli@11.4.0`(mmdc)。
- [ ] COPY mermaid-config.json / puppeteer-config.json / mermaid.css 到 `/etc/`。
- [ ] uv 装 + `uv sync --frozen --no-dev`。
- [ ] 多阶段:frontend-builder(node:20-alpine + pnpm) → COPY dist 到 `/app/frontend/dist`。
- [ ] cron crontab `0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh`。
- [ ] supervisord.conf COPY + ENTRYPOINT entrypoint.sh + CMD supervisord -n。

#### entrypoint.sh(D-O)

- [ ] **顺序**:导出 env 到 /etc/bid-app.env(给 cron) → pg_isready 等 60 次 → **`alembic upgrade head` 同步执行(失败 set -e 退出)** → `exec "$@"`(D-O)。
- [ ] **不**在 entrypoint 起 cron(由 supervisord [program:cron] 接管)。

#### supervisord.conf(§17.2)

- [ ] `nodaemon=true`;[uvicorn]、[arq-worker]、[program:cron] 三程序;stopwaitsecs 合理(uvicorn 10s / arq-worker 30s)。

#### docker-compose.yml(§17.3 / D-R / D-V)

- [ ] **bind mount**:`/var/lib/bid-app/{projects,backups,postgres-data,redis-data}` 而不是 named volume;`/etc/localtime` ro 同步时区。
- [ ] **单一 `.env`**(D-R):`env_file: .env`;不再用多文件。
- [ ] postgres 16-alpine + healthcheck `pg_isready`;`docker/init-test-db.sh:/docker-entrypoint-initdb.d/10-init-test-db.sh:ro`(D-DS / D-EA)。
- [ ] redis 7-alpine + `--maxmemory-policy noeviction`(D-V)+ `--appendonly yes`。
- [ ] depends_on `service_healthy`;app healthcheck `/health`(D-G)。

#### docker-compose.dev.yml(M5 Day1)

- [ ] 与生产相比缺少 app 容器自身;只起 db + redis 给本机后端连;volume / port / env 一致。

#### .env.example + .env(§6 / D-R)

- [ ] **不**写 `DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:...`(env_file 不展开 ${VAR},D-W);DSN 由 `config.py` property 拼。
- [ ] 包含 R10 警告(明文写"BID_APP_MASTER_KEY 泄漏 → 旋转流程见 §24.3");gen-secrets.sh 替换 __GENERATE_ME__ / __64_HEX_CHARS__ 占位符。
- [ ] master_key 64 hex chars;jwt_secret;login_fail_max_per_minute=5;login_lock_seconds=300;daily_upload_quota_mb=500;single_chapter_timeout_seconds=600;max_concurrent_projects=10。

#### scripts/(M5 Day1)

- [ ] `install.sh`:mkdir 4 个目录 + chown 999:999 postgres-data + chown 1000:1000 projects/backups。
- [ ] `gen-secrets.sh`:`cp .env.example .env` + sed 替换占位符;自检不残留 __GENERATE_ME__;Python 生成密钥(避免 openssl 差异)。
- [ ] `restore-backup.sh`:停 app → drop db → create db → pg_restore → 起 app。
- [ ] `create-test-db.sh`(D-DV):`SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}_test'` 检查后 CREATE,显式幂等;**包含说明:已有 postgres 数据卷的环境必须手动跑此脚本,docker initdb 不补跑**。
- [ ] `pg-backup.sh`:从 /etc/bid-app.env 取 env;`pg_dump -F c` 写 `${BACKUPS_DIR}/bid_${TS}.dump`;.partial → mv;7 天滚动删除。

#### README.md / 启动横幅(M5 Day1 / FR-6.5)

- [ ] README 部署 / 备份 / `create-test-db.sh` 流程清楚;含 "已有 postgres 数据卷的环境必须手动跑 scripts/create-test-db.sh"(D-EA / D-DV)。
- [ ] 启动横幅(stdout):**⚠️ 默认 admin/admin123 请立即改密** + master_key 哈希(SHA256 前几字节)+ master_key 长度校验提示。

#### §23 验收 checklist 走查(写到 `app/ACCEPTANCE_AUDIT.md`)

23 条逐项核查代码 / 配置层面是否就绪(不真跑测试):

- [ ] M0 Pandoc smoke + Word 能打开(D-DZ / D-ED 收窄口径)
- [ ] 工作流端到端 ≥ 8000 字
- [ ] API Key 真快照 FR-7.6
- [ ] 提纲确认 P4 编辑路径
- [ ] queued 排队 11 个项目
- [ ] 章节 failed → retry → retry_count=0 + abandoned + 新版本
- [ ] 章节超时 10 分钟 failed
- [ ] revise → +1 → max+1 自动 skip(`>` 而非 `>=`)
- [ ] DOCX 中文 mermaid + 表格 Word 打开
- [ ] DOCX 串行(Redis 锁)
- [ ] DOCX 缓存命中 cached=true
- [ ] 下载 Content-Disposition 含 `项目名_技术方案_YYYYMMDD.docx`
- [ ] 前端 8 页面无 console error
- [ ] failed 章节红标 + retry 按钮
- [ ] 改密前 428
- [ ] 登录失败锁 5 分钟(D-Q)
- [ ] 登录成功清零
- [ ] 全局限流 100/min
- [ ] 安全头三连
- [ ] 上传配额 500MB → 413
- [ ] API Key DB 是 bytes
- [ ] Project.encrypted_api_key_snapshot 也是密文(与 ApiKey 解耦)
- [ ] 默认 admin/admin123 + 必须改密
- [ ] DashScope banner 登录后显示
- [ ] `docker compose up -d` healthcheck 全过
- [ ] entrypoint 顺序 alembic → uvicorn(D-O)
- [ ] bind mount 生效 `/var/lib/bid-app/projects/`
- [ ] 6h 压力测试无 OOM(代码层面 Redis noeviction + arq max_jobs)
- [ ] cron pg_dump 落 `/var/lib/bid-app/backups/bid_*.dump`
- [ ] `pg_restore --list` 列出 10 张表
- [ ] 容器重启后 in-flight workflow 续跑(reconcile + LangGraph checkpoint)

---

## 审查记录(实际执行时填写)

### REVIEW-1(M0 + M1 后端)— 2026-05-03

审查范围:M0 commits `2be3028` / `d372d8d` / `26ba2a3` / `dd4dbcf` / `9071dde` + M1 commits `a14df32` / `63e9781` / `d54a8bd` / `e747fff` / `7dca873` / `84b9967` / `1208168` / `2a189d1` / `44e974c`。

#### 🔴 严重问题(已 SendMessage backend-lead)— **2026-05-03 已修复(commit `b80f4c0`)**

1. **`extract_for_project()` 从未读取真实文档,所有 M1+ 项目工作流以空 markdown 起跑 → LLM-1 hallucinate**(`services/document_extractor.py:80-93`)
   - **现状**:`extract_for_project` 用 `getattr(doc, "stored_path", None) or getattr(doc, "path", None)` 取路径,但 `Document` 模型(`models/document.py`)**只有 `markdown_path` 字段**,没有 `stored_path` / `path`。
   - **影响**:每次循环 `getattr` 返回 None → `if not path: continue`,函数返回 `{"tech_spec_md": "", "scoring_md": "", "template_md": ""}`。`worker/tasks.py:160 build_initial_state` 把空字符串塞进 WorkflowState,LLM-1 提纲根据空 prompt 生成,内容与用户上传的 3 份文档完全无关。**完全破坏 P3-P4-P5 业务正确性**。
   - **覆盖范围**:所有走 `/start` → arq worker → graph 的真实路径都中招。CLI `run_local` 用 `extract_files`(不调本函数)所以 M0 smoke 还能跑通。
   - **修复**:`Document.markdown_path` 已经存的就是 markitdown 抽取后的 .md 文件路径(见 `api/projects.py:295-298 + :310`),直接读它即可:
     ```python
     md_path = getattr(doc, "markdown_path", None)
     if not md_path:
         continue
     try:
         out[kind_to_field[kind]] = Path(md_path).read_text(encoding="utf-8", errors="replace")
     except Exception:
         log.exception("read_markdown_failed", path=md_path, kind=kind)
     ```
   - **责任**:backend-lead M0-5 / M1-5。需要立即修。
   - ✅ **解决**(commit `b80f4c0`):改读 `markdown_path`(已 markitdown 抽取的 .md 文件);多份同 kind 时 ORDER BY id ASC 后写覆盖前写(取最新);失败 log 不抛(`Path.read_text(encoding='utf-8', errors='replace')`)。

#### 🟡 中级问题(不立即阻塞,但建议尽快修)

1. **`mark_chapter_versions_abandoned` 与 worker `retry_failed_chapter_task` 语义不一致** — 当前 helper 是死代码,但若未来 caller 用它会 silent abandon 错的版本子集。
   - **现状**:
     - `workflow/sync.py:178-181` helper:`UPDATE chapter_versions SET abandoned=true WHERE chapter_id=:c AND decision IS NULL AND abandoned=false`
     - `worker/tasks.py:494-500` raw SQL:`UPDATE chapter_versions SET abandoned=true WHERE chapter_id=:c AND abandoned=false`(无 `decision IS NULL` 过滤)
   - **影响**:helper 仅 abandon 未审版本(decision IS NULL);worker 把所有非 abandoned 版本都打 abandoned(包含 decision='revise' 的多轮重写历史)。FR-4.7 / spec line 263 / line 1018-1019 表述"本轮所有未审版本",但章节进入 retry 状态时,先前 revise 轮次的 ChapterVersion 已经 decision='revise',严格"未审"过滤会让 helper abandon 0 行,不达 retry 把"过期版本全标 abandoned"的目的。worker raw SQL 的口径才对。
   - **修复**:helper 应统一成 worker 口径(去掉 `decision IS NULL` 过滤),或让 worker 调 helper 而不是写裸 SQL。**推荐**改 helper:
     ```python
     "UPDATE chapter_versions SET abandoned=true "
     "WHERE chapter_id=:c AND abandoned=false"
     ```
     然后 worker 改成调用 helper,保持一致。
   - **责任**:backend-lead 后续小补丁即可。
   - ✅ **解决**(commit `b80f4c0`):抽 `_mark_chapter_versions_abandoned_in_session(session, chapter_id)` 私有 helper(不开事务),公共入口 + worker 都复用,SQL 单一信源。worker `retry_failed_chapter_task` 把 abandon + ReviewEvent + chapter status 切换塞进同事务。

2. **SPA fallback 端点缺失**(spec §15.6 / `main.py` 末尾)
   - **现状**:`main.py:117-133` 注册了 8 个 router 后没有 `@app.get("/{full_path:path}")` SPA fallback。
   - **影响**:用户在浏览器对 `/projects/123/review` 直接刷新,FastAPI 返回 404;不能走前端 React Router。**M5 部署后用户体验破**,但 M1 本身没要求 SPA fallback(任务 #14 #13 没提)。归 M5 范畴更合适。
   - **修复**:按 spec §15.6 在 `main.py` 末尾加:
     ```python
     @app.get("/{full_path:path}", include_in_schema=False)
     async def spa_fallback(full_path: str):
         if full_path.startswith("api/") or full_path == "health":
             raise HTTPException(404)
         static_dir = Path("/app/frontend/dist")
         requested = static_dir / full_path
         if requested.is_file():
             return FileResponse(requested)
         return FileResponse(static_dir / "index.html")
     ```
   - **责任**:推到 M4 / M5 收尾时统一加。

3. **`worker/lifecycle.py:28` `AsyncPostgresSaver.from_conn_string()` 调用模式可能与 langgraph-checkpoint-postgres 2.0+ API 不一致**(待验证)
   - **现状**:`saver = AsyncPostgresSaver.from_conn_string(...)` + `await saver.setup()` 直接走 — 与 spec §17.2 line 5577-5589 一致。
   - **疑虑**:`langgraph-checkpoint-postgres==2.0.25`(uv.lock 锁定版本)的 `from_conn_string` 是 `@asynccontextmanager`,返回的是 ctx mgr 不是 saver 本身。直接调 `.setup()` 会 AttributeError。
   - **影响**:worker 启动 `on_startup` 时炸,所有 workflow task 起不来。
   - **修复**:若验证为真,改成
     ```python
     async def on_startup(ctx):
         saver_cm = AsyncPostgresSaver.from_conn_string(settings.langgraph_dsn)
         saver = await saver_cm.__aenter__()
         await saver.setup()
         ctx["checkpointer"] = saver
         ctx["_saver_cm"] = saver_cm
     async def on_shutdown(ctx):
         cm = ctx.get("_saver_cm")
         if cm: await cm.__aexit__(None, None, None)
     ```
   - **责任**:让 backend-lead 在能跑 worker 的环境(docker compose)实测一次;若炸,按上述模式修。spec §17.2 也可能需要同步更新。

#### ✅ 通过项

- **10 张表模型**(`models/`):全部字段与 §8 一致 — Project.encrypted_api_key_snapshot(D-C)/ Chapter.processing_started_at(D-AR/BF) + status enum reviewing/retrying/generating(D-AI)/ ChapterVersion.abandoned(FR-4.7)/ ReviewEvent.aborted NOT NULL default false(D-BI)/ TokenUsage.project_id ON DELETE CASCADE(FR-1.6)/ DocxJob.arq_job_id nullable + finalizing/invalidated 状态 + updated_at 双触发器(D-S/BQ/CG/BH)。`models/__init__.py` 全部 export。
- **Migration 0001**(`migrations/versions/0001_initial.py`):10 张表按 FK 依赖正确顺序;**partial unique** `uq_docx_jobs_arq_job_id WHERE arq_job_id IS NOT NULL` + `uq_docx_jobs_project_inflight WHERE status IN ('pending','rendering_mermaid','pandoc','finalizing')`(D-S/BQ);**`ix_chapters_processing` partial WHERE 严格 D-BZ**:`status IN ('reviewing','retrying','generating') OR (status='pending' AND processing_started_at IS NOT NULL)`;token_usage CASCADE;ReviewEvent.aborted server_default false;**default admin seed**(bcrypt rounds=12,must_change_password=true)。
- **db.py**:async engine + async_sessionmaker + `expire_on_commit=False`(D-DH 减少 detached 风险)+ `pool_pre_ping=True`(防游离连接)。
- **deps.py M1 stub**(commit `d54a8bd` 阶段):D-EC 完整覆盖 — 读 `BID_APP_DEV_USER_ID` 或回退查 admin user;查不到抛 500 + 提示 alembic + seed。M2 完整版已替换(REVIEW-2 范围)。
- **events/bus.py**:Redis pub/sub 跨进程(D-A/B);subscribe 用 asynccontextmanager;publish lazy `start()`;UTF-8 序列化 `ensure_ascii=False`。
- **services/concurrency.py**(585 行,§10.7 全套):
  - 三态 AcquireResult(ok/full/already_active/stale_evicted)+ Lua TRY_ACQUIRE(D-AB/AF/AN/AQ)
  - HEARTBEAT/RELEASE Lua CAS 防误释放(D-AB)
  - 双 TTL D-Y(RESERVE_TTL=300,ALIVE_TTL=60,HEARTBEAT_INTERVAL=20)
  - `_evict_stale_project` SREM + DB UPDATE 绑定(D-AN)
  - `cleanup_stale_chapters`:D-BB `get_alive_project_ids()` 排除 + `CAST(:active_ids AS int[])` typed array;reviewing/retrying 60s + pending(NOT NULL 守护)60s + generating 15min(D-AR/BF/BS/BZ);D-BL/BS Project.status=awaiting_review 同步切回
  - `cleanup_stale_docx_jobs`:覆盖全 in-flight 含 finalizing(D-AY/BQ);D-BY finalizing+文件存在 → repair done;D-BH `updated_at < NOW() - 30min`
  - `wake_queued_projects`:D-AP 不用 SCARD 用 alive count;D-AX 异常 queued 直接 failed 防死循环
  - `project_heartbeat` async ctx mgr + lost_event(D-AM)
- **worker/settings.py**:全 `@func(max_tries=1)`(D-Z/AY);`functions` 直接放函数对象(D-AJ);`max_jobs = max_concurrent_projects + 2`(D-AA);3 个 cron 全部对象(D-AJ)。
- **worker/lifecycle.py**:reconcile + wake startup;shutdown close checkpointer。**遗留疑虑**见上 #3。
- **worker/tasks.py**(874 行,三类任务 + generate_docx_task):
  - 全部 `@func(max_tries=1)`,token-acquire 后立即 try/finally(D-AH),finally release_slot + wake(D-T 周期等于 task)
  - SlotLost 走 `_slot_lost_compensation`,按 decision 分流回滚(D-AW/AZ/BI/BM/BT)
  - ChapterGenerationFailed 不写 errors.log + 不 raise + project=awaiting_review(D-AU)
  - generic Exception 写 errors.log + `_fail_project_and_run`(D-BA + D-X + D-AE)
  - resume_review_task worker 入口写 ReviewEvent flush 拿 PK(D-AC/BT),decision=='revise' 才切 generating + processing_started_at=NOW()(D-AZ/BK)
  - retry_failed_chapter_task abandon + retry_count=0 + processing_started_at=NOW()(D-BS / D-AC),`graph.aupdate_state(retry_count=0)` + `astream(None)` 续跑(D-I)
- **api/projects.py**(577 行):
  - `/start`:D-AF 校验 status='init'(line 335);D-C 真快照 `project.encrypted_api_key_snapshot = api_key.encrypted_key`(line 355);单次 `try_acquire`(D-T,line 369);D-AF already_active 兜底 409;extracting / queued 分流(D-T);**D-U 补偿全包**:enqueue 失败 release + status=init + run.status=aborted(line 405-417);arq_pool 未初始化 503(line 384)
  - `POST /documents`:文件类型 + 大小白名单;**日配额聚合**(NFR-4)按 `settings.tz` `date_trunc('day', NOW() AT TIME ZONE :tz)` 聚合(line 263-281);markitdown 抽取容错;落 markdown_path
  - `DELETE`:创建者 / admin gate;commit 后 rmtree 失败仅 log
  - `PUT /outline` D-K:status='outline_ready' 校验;try_acquire 503 + Retry-After:60;enqueue resume_review_task 失败补偿 release
  - `GET /proposal` `/proposal.md`:proposal.md 文件读 + Content-Disposition 中文文件名(filename 字段)
- **api/chapters.py**:`/review` 行锁 `FOR UPDATE` + 状态 awaiting_review 校验(D-AD)→ reviewing 中间态 + processing_started_at=NOW()(D-AR)→ try_acquire 503 + Retry-After:60 → enqueue resume_review_task → **HTTPException + Exception 双补偿**回 awaiting_review + release(D-AO);ReviewEvent 由 worker 写不在 API 写(D-AC);`/retry` 同款,中间态 retrying;补偿回 failed
- **api/stream.py**:Depends get_current_user;立即 `event: ready`;心跳兼容 `await asyncio.wait_for(events.next, timeout=20)` + `: ping\n\n`;CancelledError 安静退出;`X-Accel-Buffering: no`(防 nginx buffer);返回 StreamingResponse
- **api/health.py**:仅 db + redis ping(D-G);未初始化 redis 走 "skipped" 不算 fail(主动 / 启动期容错)
- **services/llm.py**(425 行,§11.1):
  - `call_llm_stream`:`async with asyncio.timeout(SINGLE_CHAPTER_TIMEOUT_SECONDS)` 包整个流式收集(D-D);外层 except TimeoutError → `LLMTimeoutExceeded` + errors.log(D-BG)
  - 每次重试都 `_write_llm_error(... attempt=...)`(D-BE);用尽 → `LLM exhausted`
  - `call_llm_json`:同款总超时 + JSON parse 失败 _write_llm_error → LLMRetryFailed 走重试链(D-BO)
  - `ChapterGenerationFailed` 异常类(D-AU);`_FAKE` env 路径(测试友好)
- **workflow/state.py**:5 个 Loop 变量名严格(D-EE 头),不放 api_key(D-C)
- **workflow/graph.py**:**严格 11 节点**(D-EE 三节点拆分回归);header 注释引用 #37 audit deviation 论据;CLI 路径 `checkpointer=None` 允许 in-memory 跑
- **outline_review / human_review / merge_chapter / pick_chapter / parse_outline / write_chapter / gen_visuals / update_state / assemble**:
  - update_state 用 `>` 而非 `>=`(spec line 1587)+ D-BK processing_started_at on revise→generating
  - write_chapter D-AU ChapterGenerationFailed wrap + D-BF processing_started_at + D-C Project.encrypted_api_key_snapshot 读路径
  - assemble D-CG + D-CM 全 in-flight invalidate;CLI 容错(catch-all)
  - parse_outline JSON 容错 + 字段 setdefault + Loop var 重置
- **workflow/sync.py**:D-BP 白名单 + isidentifier 双过滤;`save_chapter_version` 自动 `MAX(version)+1`;`record_review_event` 单事务 flush 拿 id
- **CLI**:`run_local` 走 `extract_files`(不依赖 broken `extract_for_project`)+ 完整交互式审核;`test_llm` 三模型 smoke
- **services/api_key_validator** + **services/docx_export**(M0 smoke 部分)+ **core/error_log**(D-AE JSONL 格式)+ **services/token_usage**(`uid<=0` skip + project_id<=0 → NULL)

#### 🟡 非阻塞建议(留作 nitpick)

- M1 / `services/document_extractor.py:55-59`:`extract_for_project` 返回类型注释是 dict[str, str] 但若文件读失败可能塞 None;现在 catch-all 把异常吞了所以仍是 str — 可省略 .read_text 异常返回 ''。
- M0 / `workflow/prompts/write_chapter_prompt.py:110`:`target_chars = target_pages * 800`;spec §10.3 line 1483 写 "约 600 字"。代码内部一致(800),但与 spec 微差。建议要么改成 600,要么 spec 同步成 800。
- M1 / `api/health.py:46`:`v.startswith("skipped")` 也算 ok,与 spec line 5028 "200 if all == ok else 503" 略宽松;启动期容错合理,建议保留并在 docstring 注明。
- M1 / `worker/lifecycle.py` 的 `from_conn_string` 调用模式见上"中级问题 #3",待运行时验证。
- M1 / `api/stream.py:14` docstring 说 "M1 阶段 stub";M2 完整版已替换 — 可同步更新注释。
- M0 / `workflow/nodes/extract_documents.py:31-41` fallback try/except `from .document_extractor import` — 现在 services 已经存在,这个 fallback 永远不会触发。**冗余**,可清。

### REVIEW-2(M2 + M3 后端)— 2026-05-03

审查范围:M2 commits `0ed5608` / `f14e077` / `d37b6de` / `9a4aa6c` / `14f14ea` + M3 commits `c7f2e56` / `afcc307` / `6d64d96`(+ M3-4 #23 reference.docx 占位 + mermaid 中文配置已在 M5 走 docker/ 验过,见 REVIEW-4)。

team-lead 同步 backend-lead 三处遗留点已分别核查(见下"已知遗留核查"段)。

#### 🔴 严重问题(已 SendMessage backend-lead)— **2026-05-03 已修复(commit `97cc5bc`)**

1. **`_resolve_api_key` CLI fallback 在生产路径**有 silent ApiKey 真快照绕过风险**(`workflow/nodes/{write_chapter,gen_visuals,generate_outline}.py` 三处)
   - **现状**(以 `write_chapter._resolve_api_key:32-63` 为代表):
     ```python
     try:
         from ...core.crypto import decrypt_api_key
         from ...models import Project
         ... # 查 Project.encrypted_api_key_snapshot
         if encrypted is not None:
             return decrypt_api_key(encrypted)
     except Exception:
         pass

     cli_key = os.environ.get("BID_APP_CLI_API_KEY")
     if cli_key:
         return cli_key
     raise RuntimeError(...)
     ```
   - **问题 1(silent fallback on `encrypted is None`)**:`Project.encrypted_api_key_snapshot` 为 NULL 时(/start 部分失败、人工 DB 改动、未来代码 bug 把 snapshot 清了),代码**不报错**,直接走到 `cli_key = os.environ.get(...)`。如果生产容器恰好设了 `BID_APP_CLI_API_KEY`(开发者跑容器跑过测试残留 / docker compose 继承宿主 env / .env 文件被人手改加了这一行),worker **悄悄用环境变量里的 key 跑 LLM**,不是用户的 ApiKey。
   - **问题 2(silent fallback on decrypt error)**:master_key 错位 / blob 损坏 → `decrypt_api_key` 抛 InvalidTag → `except Exception: pass` 吞掉 → 同样落到 CLI fallback。
   - **影响**:违反 D-C "真快照"语义(运行时只从 Project.encrypted_api_key_snapshot 读)+ R10(master_key 丢/错时应当显式失败而不是降级)+ FR-7.4(API Key 安全)。**审计风险**:用户的 LLM 调用记账可能挂在错误账户,操作员 CLI 测试 key 被无意中长期消耗。
   - **运行时触发概率**:取决于生产容器是否 set `BID_APP_CLI_API_KEY`。`.env.example` / `config.py` 都没引入这个变量,所以默认部署不会有。但**没任何代码层防护**阻止它泄漏到生产 `.env`。
   - **修复**:用 `run_id` 区分 CLI / production(write_chapter 已有 `_real_run(run_id) = run_id > 0`;CLI 走 -1)。CLI fallback 仅在 `not _real_run(run_id)` 时启用;生产路径若 snapshot 缺失或 decrypt 失败,**显式 raise**,worker 走 `except Exception: 顶层 _fail_project_and_run`(D-BA),用户从 P5 看到 project failed + errors.log 真实原因。
     ```python
     async def _resolve_api_key(project_id: int, run_id: int | None = None) -> str:
         from ...core.crypto import decrypt_api_key
         from ...models import Project
         try:
             async with session_factory() as s:
                 row = await s.execute(
                     select(Project.encrypted_api_key_snapshot).where(
                         Project.id == project_id
                     )
                 )
                 encrypted = row.scalar_one_or_none()
         except Exception as e:
             # 真生产 DB 不可达 → 让 worker 顶层 catch,fail 项目;CLI 路径才允许 fallback
             if run_id is not None and run_id > 0:
                 raise RuntimeError(f"db error resolving api_key: {e}") from e
             encrypted = None

         if encrypted is not None:
             try:
                 return decrypt_api_key(encrypted)
             except Exception as e:
                 if run_id is not None and run_id > 0:
                     raise RuntimeError(
                         f"decrypt api_key failed (master_key 不一致?): {e}"
                     ) from e
                 # CLI 路径才允许 fallback
                 pass

         # 仅 CLI 路径(run_id<=0)允许 BID_APP_CLI_API_KEY fallback
         if run_id is None or run_id <= 0:
             cli_key = os.environ.get("BID_APP_CLI_API_KEY")
             if cli_key:
                 return cli_key

         raise RuntimeError(
             f"project {project_id} has no api_key snapshot; did /start succeed?"
         )
     ```
   - **责任**:backend-lead — `write_chapter.py` / `gen_visuals.py` / `generate_outline.py` 三处一起修。生产路径调用方(`workflow/nodes/*` `run()`)已经知道 run_id,直接传过去即可。
   - ✅ **解决**(commit `97cc5bc`):严格按推荐 pattern 修。`run_id > 0`(production)时 DB 异常 / snapshot=None / decrypt 失败均 raise(让 worker 顶层 `_fail_project_and_run` 标 failed),完全不走 fallback。`run_id is None / <= 0`(CLI)保留旧 fallback 给 `cli/run_local`。三处节点 caller 已传 `run_id=state.get("run_id")`。同 commit 还修了 🟡 #3 nullable scalar_one()。

#### 🟡 中级问题

1. **`/api/auth/login` 设了 `refresh_token` cookie 但没有 `/api/auth/refresh` 端点消费它**(`api/auth.py:78-85`)
   - **现状**:login 时 `set_cookie("refresh_token", create_refresh_token(...), path="/api/auth/refresh", ...)`,logout 时 `delete_cookie("refresh_token", path="/api/auth/refresh")`。但仓库里搜 `/refresh` 路由 → 不存在。
   - **影响**:access token 2h 过期后,前端拿到 401 → /login(apiFetch 已实现)→ 用户重新登录。**功能不缺失,只是 refresh token cookie 是死数据**。spec §14.6 也只列 login/logout/me,没列 /refresh。
   - **建议**:要么(a)按 spec 砍掉 `refresh_token` cookie 的 set/delete(避免死代码 + 减少 cookie 体积);要么(b)未来加上 /refresh 端点用 refresh_token 换新 access。**当前不阻塞**;一致性 nit。

2. **`workflow/sync.py` 顶部延迟 import 是 forward-port 残留**(team-lead 已知遗留 #1 核查结果)
   - **现状**:`sync_outline_to_db:94` `save_chapter_version:138` `record_review_event:197` 都把 `from ..models import Chapter|ChapterVersion|ReviewEvent` 放函数内部。
   - **核查**:M1 落库后 models 一直存在,延迟 import 不解决任何 circular dep(models 不依赖 sync)。**不引入循环**也**不 hide 真 import bug**(若 models 写错 attribute,函数运行时还是会抛)。**纯历史包袱**。
   - **建议**:可以提到模块顶层。**不阻塞**,清理性改动。

3. **`_resolve_user_id` 在 `nullable=True` 字段上 scalar_one()**(`workflow/nodes/{write_chapter,gen_visuals,generate_outline}.py`)
   - **现状**:
     ```python
     row = await s.execute(
         select(Project.api_key_owner).where(Project.id == project_id)
     )
     return row.scalar_one()
     ```
     `Project.api_key_owner` 是 `Mapped[int | None]`,但 `.scalar_one()` 不允许 None — 行存在但字段为 NULL 时 OK(返回 None),行不存在时 raise NoResultFound。这里逻辑想要的是"行肯定存在,字段可能 NULL"。**问题**:如果 /start 没设 api_key_owner(理论不应该,但有可能),后续 `record_token_usage(user_id=None)` 会被调用,token_usage.py 的 `int(None)` 抛 TypeError → token_usage 静默 log 跳过。无业务影响,但行为模糊。
   - **建议**:`return row.scalar_one_or_none() or 0`(让 token_usage skip 路径主导);或在 /start 强制非空检查 api_key_owner。**不阻塞**。

4. **`worker/tasks.py:retry_failed_chapter_task` raw SQL abandon 不调 `mark_chapter_versions_abandoned` helper**(REVIEW-1 已记)
   - **状态**:沿用 REVIEW-1 中级问题 #1,未变。helper 死代码,worker raw SQL 是真口径。建议合二为一(改 helper 去掉 `decision IS NULL` 然后 worker 调它)。

#### 已知遗留核查(team-lead brief)

1. **`workflow/sync.py` 顶部延迟 import** — 见上"中级问题 #2"。**结论**:无循环依赖,无 hide bug;**纯历史**包袱,清理性改动。
2. **`_resolve_api_key` CLI fallback** — 见上"严重问题 #1"。**结论**:**真问题**,生产 silent fallback 风险,违反 D-C / R10 语义。已升 🔴。
3. **`worker/tasks.py:build_initial_state` 不带 run_id<0 守护** — `build_initial_state:148` 接 `(project_id, run_id)`,内部直接读 DB(`SELECT pages_per_chapter ...`)+ 调 `extract_for_project(project_id)`。**核查**:CLI `run_local.py` 不调 `build_initial_state`(自构造 state,line 113+),所以 caller 级隔离完整。**结论**:不是 bug,run_id<0 守护在 caller 级(CLI 走另一条路)而不是 callee 级,设计合理。

#### ✅ 通过项

- **`core/crypto.py`**:AES-GCM + 12B nonce + length guard ≥13;`_KEY = bytes.fromhex(settings.bid_app_master_key)`(config.py 已强校验 64 hex)
- **`core/security.py`**:bcrypt rounds=12;`verify_password` catch ValueError(防 hash 损坏 crash);JWT HS256 + `kind` 字段强校验防 access-as-refresh
- **`core/rate_limit.py`**:slowapi `Limiter(default_limits=[settings.global_rate_limit])`;`storage_uri=settings.redis_url`(跨进程一致)
- **`core/login_throttle.py`**:`record_fail` INCR + 首次 EXPIRE 60s + n>=5 SET lock EX 300s(D-Q / FR-6.7);`clear_fails` 仅删 fail key,不动 lock(让锁等过期);`is_locked` get lock key
- **`core/security_headers.py`**:CSP 完整(default-src 'self' / img data:/blob: / style 'unsafe-inline' / script 'self' / connect 'self' / font data: / frame-ancestors 'none');X-Content-Type-Options nosniff / X-Frame-Options DENY / Referrer-Policy no-referrer 全;用 `setdefault` 不覆盖端点自定义
- **`core/middleware.py` TraceIdMiddleware**:`X-Trace-Id` header 复用或 uuid.uuid4().hex[:16];`structlog.contextvars.bound_contextvars` 上下文绑定;响应回写 header
- **`main.py` 中间件注册顺序**:LIFO 倒序写 — `add_middleware(SecurityHeaders) → SlowAPI → TraceId`,实际请求顺序 TraceId(最外)→ SlowAPI(限流)→ SecurityHeaders(响应头);`app.state.limiter` 注册 + `_rate_limit_exceeded_handler` exception handler;lifespan 起 redis client + arq_pool
- **`deps.py` 完整版**(commit `d37b6de`):
  - `get_current_user`:cookie access_token → `decode_token('access')` → `db.get(User, user_id)` → 不活跃 401 → must_change_password 抛 **428 + detail={"error": "must_change_password"}**(D-F)
  - `get_current_user_lax`:不查 must_change_password(豁免端点)
  - `require_admin`:`Depends(get_current_user)` + `role!='admin' → 403`(经过严格层,管理员也得 must_change_password=false)
  - **`BID_APP_DEV_USER_ID` 分支已移除**(M2 起走真登录)
- **`api/auth.py`**:
  - `/login`:`is_locked → 429`;`record_fail` 失败 (锁定 → 429 / 普通 → 401);`is_active=false → 403`;成功 `clear_fails` + `last_login_at=NOW` + JWT cookies(httponly / samesite=strict / max-age 2h+7d)
  - `/logout`:lax 豁免改密前能登出;删两个 cookie
  - `/me`:lax,前端登录后能拉用户信息渲染 UI(若 strict 改密前会 428 锁死)
- **`api/me.py`**:
  - `/change-password`:lax + 旧密码校验 + 新密码 ≥ 8 chars + 不能与旧相同;成功 `must_change_password=false`
  - `/api-key` GET:masked `sk-***xxxx`;**永不返回明文**(FR-7.4);decrypt 失败兜底 `***`
  - `/api-key` PUT:**先调 `validate_dashscope` 再 encrypt+存**;upsert 走 `(user_id, provider)` UniqueConstraint
  - `/api-key/test`(D-G / §15.5):用已存的 key 调一次连通,**失败不抛 4xx 返 `{ok: false, error}`**(让前端按需展示);成功刷 `last_validated_at`
  - `/api-key` DELETE:幂等(不存在直接 ok)
  - `/token-usage`:period ∈ {month, all},按 model 分组聚合;`SUM(...)::bigint` 防 SQLite 测试库类型推断踩坑
- **`api/admin.py`**:
  - `dependencies=[Depends(require_admin)]` router 级 gate
  - `POST /users` 强制 `must_change_password=true` 创建;username 冲突 409
  - `PATCH /users/{id}`:**防 admin 把自己降权**;`reset_password` 同时 set must_change_password=true
  - `DELETE /users/{id}`:**防 admin 删自己**;Project.created_by RESTRICT 时 catch + 409
  - `/token-usage`:全局聚合,JOIN users 拿 username
- **`services/api_key_validator.py`**:`asyncio.timeout(30)` + `litellm.acompletion(model=llm3_visuals_model, max_tokens=1)` 最小请求;catch 所有 litellm 异常 + TimeoutError 转 `ApiKeyValidationFailed`;**不调 `record_token_usage`**(避免把 validator 测试 token 算进配额)
- **`services/docx_export.py`(完整版)**:
  - `_module_lock` `await asyncio.wait_for(.acquire(), timeout=120)`(D-BR)
  - `_redis_lock` `r.lock(... timeout=300, blocking=True, blocking_timeout=120, thread_local=False)`;`acquired=False` raise TimeoutError
  - MERMAID_RE 兼容 ``` / ~~~ 围栏 + 行尾空格 + CRLF(D-N)
  - `re.finditer` + 反向 span 替换;失败 fence 保留(降级容错)
  - mmdc 命令:`-c /etc/mermaid-config.json -p /etc/puppeteer-config.json --cssFile /etc/mermaid.css`(§13.2)
  - **写到 `proposal.{job_id}.tmp.docx`**(D-BN);返回 tmp_path 让上层 atomic rename
  - `on_stage("pandoc")` 在 mermaid 完成后、pandoc 启动前调;**不再 try/except 兜底**(D-CB);`_StaleJob` 必须透传到 task 顶层
  - reference.docx 存在才 `--reference-doc=`;pandoc `--resource-path=work` 解析相对图片路径
  - `export_docx_smoke` 保留给 CLI(M0 验收口径)
- **`worker/tasks.py:generate_docx_task`(REVIEW-1 已读 875 行,M3-2)**:
  - `@func(max_tries=1)` 与其他 task 一致(D-AY)
  - 入口校验 row 存在 → `done/failed/invalidated` 直接 return(D-AK / D-CM)
  - `pending → rendering_mermaid` `WHERE status='pending'` + rowcount=0 → return stale(D-BX)
  - **进 rendering 后立即 `final_path.unlink(missing_ok=True)`**(D-CU);OSError → UPDATE failed + raise
  - `_update_stage("pandoc")` `WHERE status='rendering_mermaid'` + rowcount=0 → raise `_StaleJob`(D-BX)
  - export_docx 异常 → `UPDATE failed WHERE status IN (4 个 in-flight)`(D-BH/BQ)
  - **抢 finalizing**:`UPDATE WHERE status IN ('pending','rendering_mermaid','pandoc')` + rowcount 守护(D-BQ)
  - **rename 之前再查一次 status**:`invalidated` → unlink tmp + return invalidated(D-CQ)
  - **rename 在 done 之前**:`tmp_path.rename(final_path)` → 失败 UPDATE failed + unlink tmp + raise(D-BN/BQ)
  - `_commit_docx_done`:`UPDATE done WHERE status='finalizing'` + rowcount 守护;rowcount=0 SELECT 当前状态分类(done/invalidated/其它)→ best-effort unlink + return invalidated(D-CE/CL/CQ/CV)
- **`services/concurrency.py:cleanup_stale_docx_jobs`**(REVIEW-1 已 ✅,本轮再核 finalizing repair / 全 in-flight failed 链路无变化)
- **`api/docx.py`**:
  - POST cached 分支:`cached.exists() AND latest.status='done'` 才命中(D-CK + D-CJ);返回 latest done docx_job_id(前端轮询入口)
  - POST 非 cached:**先 commit pending 行 → 再 enqueue → 再回写 arq_job_id**(D-AK);enqueue 失败 / 返回 None → UPDATE failed + 503;arq_pool 未初始化 503;IntegrityError 409
  - GET `/docx-job/{id}`:**inline finalizing repair**(D-CD);finalizing → repair done(用 RETURNING 更新 row);D-BU/CN 公开状态映射 finalizing → "processing";stage 中文文案
  - GET `/proposal.docx` 下载:**inline finalizing repair**(D-CO);D-CJ 拒分支(invalidated → 409 docx_invalidated;非 done → 409 docx_not_ready;done 但文件不存在 → repair failed + 409 docx_missing);`Content-Disposition` `filename*=UTF-8''<encoded>` + ASCII fallback(FR-5.6)
- **`templates/reference.docx` 占位 + mermaid 中文配置**:reference.docx M0 阶段是 .gitkeep 占位(M3 / M5 部署时由运维/打包者手作 LibreOffice 模板);M5 mermaid 三件套已经验过(REVIEW-4 ✅)

#### 🟡 非阻塞建议(留作 nitpick)

- M2 / `api/auth.py:78-85`:`refresh_token` cookie 设了但没有 `/api/auth/refresh` 端点消费 — 死数据;改成不 set 或加端点(详见上"中级问题 #1")。
- M2 / `workflow/sync.py:94/138/197`:延迟 import 历史包袱,可清理到模块顶层(详见上"中级问题 #2")。
- M2 / `workflow/nodes/*._resolve_user_id`:`scalar_one()` 在 nullable 字段上,行存在但字段 NULL 时返回 None,后续 token_usage.int(None) 静默 skip — 不算 bug,但行为模糊。建议 `scalar_one_or_none() or 0`。
- M3 / `services/docx_export.py:80-81`:`_ = project_name`(防 lint unused)和 export_docx 签名里的 `project_name` 参数实际没在内部用,只为 caller API 签名清晰。可考虑去掉参数(或在 docstring 注明"caller 用,internal pass-through")。
- M3 / `services/api_key_validator.py:22`:`_TEST_MODEL = settings.llm3_visuals_model`(模块导入时取值)。如果运维改 .env 后**不重启**,validator 仍用旧模型。settings 是单例,不影响。但配 hot-reload 时要注意。
- M2 / `api/me.py:160` `/api-key/test` 返回 `last_validated_at` 是个 sa.func 表达式(line 189 SET 的是 `sa.func.now()`),返回时是 DateTime 还是 None?ORM 已 commit + 在 session 里 refresh 后能拿到值。当前没显式 `db.refresh(api_key)`,在 expire_on_commit=False 下 ORM 仍能读已设的属性 — **OK**,但拿到的是 server-side func 表达式还是真值取决于 SQLAlchemy 版本。建议显式 `await db.refresh(api_key)` after commit 拿真值。

### REVIEW-3(M4 前端)— 2026-05-03

审查范围:M4 commits `b64419f`(#26 vite scaffold)/ `8c891e1`(#28 auth pages)/ `cd8b00c`(#25 projects pages)/ `1426712`(#24 chapter review)/ `d6e0229`(#27 proposal+admin+banner)/ `33539fd`(#25 收尾 docs list)。frontend-lead 自验:`tsc -b` 0 错,`vite build` 5.4s,dev server 全 10 路由 200。

#### 🔴 严重问题

**无**。M4 前端整体质量很高,所有 spec §16 + REQUIREMENTS P1-P8 硬指标全部通过。

#### 🟡 中级问题(留作 nit,不阻塞)

1. **`useSSE.ts` ProjectEventType 联合缺 3 个后端事件**(`hooks/useSSE.ts:9-21`)
   - **缺**:`extract_documents_passthrough`(`extract_documents.py:27`)、`extract_documents_done`(`:49`)、`outline_started`(`generate_outline.py:104`)
   - **影响**:仅 TS 类型层面,运行时 JSON.parse 是 any,事件不会丢失;但前端 ProjectEventType 用作 exhaustive switch 时不全。当前消费者(ChapterReviewPage)用 if/else 链,fallthrough 分支什么都不做,**无功能影响**。
   - **建议**:补全联合(3 个事件即使 UI 不响应,至少类型层面知道存在)。

2. **`types.ts` ChapterVersionDTO.text 字段名与后端 `ChapterVersion.body_markdown` 不一致**(`lib/types.ts:123`)
   - **现状**:后端 `models/chapter_version.py:30` 字段是 `body_markdown`,前端 DTO 写 `text`。
   - **影响**:目前后端没有 `/chapter-versions` 端点,DTO 仅在 mock 中使用,**当前无运行时影响**。但未来如果加端点会撞 contract。注释 line 119 已标注"M2/REVIEW-2 暂未提供端点"。
   - **建议**:重命名为 `body_markdown` 与后端对齐;mock 同步改。

3. **`docker-compose.dev.yml` 通过 vite proxy 转发 SSE,默认配置可能不可靠**(`vite.config.ts:14-17`)
   - **现状**:`'/api': 'http://127.0.0.1:12123'` 简写;http-proxy-middleware 默认对 chunked HTTP 响应是支持的,但有些版本/环境对 `text/event-stream` buffer 不可控。
   - **影响**:dev 模式下 SSE 流式 token 可能延迟或被批量送达,生产部署直连 uvicorn 不受影响。frontend-lead 自验 dev 路由 200 但未验证 SSE 流式延迟。
   - **建议**(可选):若开发期 token 延迟明显,改为完整对象配置:
     ```ts
     '/api': { target: 'http://127.0.0.1:12123', changeOrigin: true, ws: false }
     ```
     或显式禁用 buffering。生产环境无影响。

4. **`mock.ts` 813 行 fixtures 在生产 build 中可能未完全 tree-shaken**(`lib/mock.ts`)
   - **现状**:`isMockEnabled()` 在 prod 评估为 false(因 `import.meta.env.PROD` 内联),Vite/Rollup *应当* eliminate 不可达分支。但 `MockProjectEventSource` 类被 useSSE.ts 顶部 import,**始终留在 bundle**;它依赖大量 fixture 数据。
   - **影响**:生产 bundle 体积可能多几十 KB minified。功能完全正确(运行时 mock 不被调用)。
   - **建议**(可选):用动态 import 把 mock 数据加载延后到 `isMockEnabled()` 真时,或在 vite.config 加 `rollupOptions.external` 对 prod 排除 mock.ts。**当前不阻塞**;FE 团队验收时跑 `vite build && du -sh dist/` 看实际尺寸即可。

5. **`AdminPage.tsx:74` 用 `window.prompt()` 收集重置密码,密码明文显示**
   - **现状**:`const newPwd = window.prompt('为 ${username} 重置密码(≥ 8 位):')` — 浏览器原生 prompt 是可见输入。
   - **影响**:仅内部 admin 操作,但 admin 旁边有人路过能看到密码。轻微 UX/安全问题。
   - **建议**:改成 Dialog + Input type="password";不阻塞 MVP。

6. **`SettingsPage.tsx` API Key delete 用 `window.confirm`**
   - **现状**:同 5,简单但够用。**OK**,nit 而已。

#### ✅ 通过项(完整列出 spec 硬指标)

- **路由 + RequireAuth**(`router.tsx` + `components/RequireAuth.tsx`):
  - 10 个路径全:`/login` `/change-password` `/` `/projects/new` `/projects/:id/upload` `/projects/:id/outline` `/projects/:id/review` `/projects/:id/proposal` `/settings` `/admin` + `*` fallback
  - `RequireAuth` 检查 `must_change_password` → 跳 `/change-password`(`allowMustChange` 例外);`requireAdmin` 检查 role!=admin → 跳 `/`(D-F 兼容)
  - `AppShell` 包装提供顶部导航 + DashScopeBanner;`/login` `/change-password` 不挂壳
  - `Authed` helper 简化 RequireAuth + AppShell 嵌套;admin 路由独立 `RequireAuth requireAdmin`(经过 strict 层)
- **`apiFetch.ts`**:
  - 401 → /login 重定向 + `redirect()` 防止 location 已在目标时重复跳
  - 428 → /change-password 重定向(对应 D-F)
  - `PASSTHROUGH_PATHS = ['/api/auth/login', '/api/auth/refresh', '/api/auth/logout']` 防登录失败循环
  - mock 层 `isMockEnabled()` 切换;mock 模式下 401/428 也走 redirect(passthrough 例外)
  - `credentials: 'include'`(JWT cookie httpOnly);FormData 不强加 JSON content-type
  - 204 → null;非 ok → throw ApiError;响应体 JSON parse fallback 到 text
  - `readApiError` helper 处理 FastAPI 多种 detail shape(string / {detail: ...} / {detail: {error/message: ...}})
- **`hooks/useSSE.ts`**:
  - EventSource + `withCredentials: true`;mock 模式用 MockProjectEventSource
  - 心跳兼容:后端 `: ping\n\n` 是注释行,浏览器不触发 onmessage(line 61 注释明确)
  - `handlerRef` + `optionsRef` ref 模式防止 onEvent 引用变化重订阅
  - `useEffect` 仅 deps `[projectId, options.enabled]`;cleanup `es.close()`
  - JSON.parse try/catch + `console.warn`(不 crash)
  - EventSource onerror 不主动 close(让浏览器自动重连 ~3s);只 callback 通知
  - `onopen` / `onerror` 选项让消费者可挂 hook
- **`hooks/useAuth.ts`**:`useCurrentUser` `retry: false` + `staleTime: 60s` 减少 401 风暴 + 服务器压力;`useLogout` `qc.clear()` 全量清缓存;`useChangePassword` invalidate `['auth', 'me']`
- **`hooks/useToast.tsx`**:Provider + setTimeout 自动 dismiss(默认 4s);button 点击手动 dismiss;5 个 variant
- **`lib/markdown.tsx` MarkdownRenderer**:
  - mermaid 全局 `mermaid.initialize` 单例(`mermaidInitialised` flag,避免每次渲染重 init)
  - `securityLevel: 'loose'` 兼容中文 token
  - 中文字体 stack:`-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif`(Mac / Windows / Linux 全覆盖)
  - `cancelled` flag 防止 unmount 后 setState
  - 渲染失败 fallback 成 `<pre>` 代码块(降级容错,**不阻塞预览**)
  - 唯一随机 `id` 防 SVG 命名冲突
  - `remarkGfm` + `rehypeRaw` 支持 GFM 表格 + 嵌入 HTML
  - code 组件 fence 检测 `language-mermaid`,其它语言原样 `<code className>`
- **8 个用户页面**(LoginPage / ChangePasswordPage / ProjectListPage / NewProjectPage / DocumentUploadPage / OutlineConfirmPage / ChapterReviewPage / ProposalPage / SettingsPage / AdminPage,共 10):
  - 全部用 zod / pydantic-mirror 客户端校验;错误回填到 form errors state
  - 全部用 `readApiError` 提取后端错误 detail 显示在 toast
  - destructive 操作(删项目 / 删用户 / 禁用 / 删 API Key)均 `window.confirm` 二次确认
  - loading / error / empty 三态友好提示;`!project.data` 展示"项目不存在或无访问权限"
  - **ChapterReviewPage**(M4 最难):
    - SSE 状态机正确处理 `chapter_started/picked` → `chapter_token` 累积 → `awaiting_review` 落 readyText → `chapter_approved/skipped/failed/max_retry_skip` 重置 streaming + refetch outline
    - `streaming.index !== activeIndex` 时不显示流式;切章节自动切到对应章节 readyText
    - `chapter_failed` 红 toast + 转到 ReviewActions retry 流(对应 F14)
    - `proposal_ready` toast + project.refetch()
    - `ChapterEmptyHint` 8 个 status 都给文案(pending / generating / retrying / reviewing / approved / skipped / failed / 默认)
    - 历史版本 Tab 显式提示"本期后端尚未提供历史版本端点"(诚实)
- **`ChapterSidebar`**:8 个 status 各自 Badge variant(失败 → destructive 红);retry_count 可选透传(M1 后端不返回但 mock 兼容)
- **`ReviewActions`**:三按钮 awaiting_review 启用,其他禁用;revise 必须有 feedback;failed → 单独 retry CTA destructive 风格;busy state 防重复提交
- **`DataExportPanel`**:复制 markdown / 下载 .md / 触发 .docx / 进度 Badge / done 显示下载按钮 / **invalidated 状态有专门"请重新生成"hint**(对应 D-CG / D-CK)
- **`DashScopeBanner`**:localStorage 一次性提示(D3);`role="status"` 无障碍
- **API hooks**(`api/{auth,me,admin,projects,chapters,docx}.ts`):
  - 每个 hook 文件头部注释明确对齐后端端点 contract,引用 spec / commit hash
  - tanstack query 命名规范:queryKey 嵌套(`['projects', id, 'outline']` / `['admin', 'users']`)
  - mutation 后正确 invalidate 相关 query
  - 404 → null 优雅处理(`useApiKeyInfo`)
  - polling refetchInterval 函数化:done/failed 停止(`useDocxJob`)
  - 文件下载用 `apiUrl()` + `<a href download>` 而不是 fetch+Blob(让 Content-Disposition 文件名生效,带 cookie)
- **`lib/types.ts`**:与后端 schemas 字段严格对齐(`UserDTO` / `ProjectDTO` / `OutlineChapterDTO` / `OutlineResponseDTO` / `OutlineChapterIn` / `DocxJobDTO` 等);ProjectStatus / ChapterStatus / DocxJobStatus 完整枚举;明确标注未上线端点(`ChapterVersionDTO`)
- **`lib/mock.ts`**:
  - `isMockEnabled()` 三层 gate:`typeof import.meta` / `PROD 必关` / `VITE_API_REAL=1 必关` — **生产 build 不夹带 mock 运行时**
  - 字段名 / 状态枚举 / 路径与后端契约严格一致(注释引用 commit 7dca873 / 2a189d1 / 44e974c)
- **`main.tsx`**:`QueryClient` `retry: 1 / refetchOnWindowFocus: false / staleTime: 30s`(合理默认);Provider 嵌套层次正确(QueryClient → ToastProvider → RouterProvider)
- **shadcn 9 件套**:Badge / Button / Card / Dialog / Input / Label / Separator / Tabs / Textarea — 风格统一,导航 / 按钮 / 表单视觉协调;管理后台 Dialog + Tabs 用法标准
- **App.tsx**:简单 `<Outlet />` 容器;`min-h-screen bg-background text-foreground` 全局背景

### REVIEW-4(M5 部署)— 2026-05-03

审查范围:M5 commits `2b7e001` / `292e974` / `8e6adaa` / `c80441e`。**§23 整体走查推到任务 #37**(本轮只看 M5 部署交付物的代码 / 配置正确性)。

#### 🔴 严重问题(已 SendMessage devops-lead)— **2026-05-03 已全部修复(任务 #38)**

1. **uv.lock 缺失,`docker build` 立刻失败**(`Dockerfile:54` `COPY backend/uv.lock` + `:55` `uv sync --frozen`)
   - **现状**:`app/backend/uv.lock` 不存在,`.gitignore` 也没有忽略它。
   - **影响**:`docker compose build` 在 COPY 阶段直接抛"file not found";`scripts/install.sh` 跑到第 [4/6] 步退出。M5 验收"fresh Linux 30 分钟"无法成立。
   - **修复**:`cd app/backend && uv lock` 生成并 commit `uv.lock`。
   - **责任**:本身是 backend M0 的产物,但 Dockerfile 假设它存在 — devops-lead 与 backend-lead 协作。
   - ✅ **解决**(commit `70278aa`):305KB lockfile,158 packages,涵盖 langgraph / litellm / arq / fastapi / sqlalchemy 等 19 个 spec 关键依赖;requires-python 与 pyproject.toml 一致(`==3.12.*`)。

2. **`scripts/restore-backup.sh` 灾难恢复顺序错误,落到 alembic 迁移过的 DB → 冲突 / 数据腐蚀**(`scripts/restore-backup.sh:75-94`)
   - **现状**:顺序是 stop app → drop db → create db → **start app(触发 entrypoint:pg_isready + alembic upgrade head + supervisord)** → exec app pg_restore。
   - **问题**:`docker compose start app` 在第 89 行触发 entrypoint.sh 把空数据库做 `alembic upgrade head`,创建全部 schema;紧接着 pg_restore 在 91 行从 dump(custom format,含 `CREATE TABLE / CREATE INDEX / CREATE SEQUENCE` 等)恢复 — 与已存在 schema 全冲突。
   - **影响**:不带 `--clean / --if-exists` 时 `pg_restore` 默认逐对象报错;不进事务时 `COPY` 偶尔成功 + DDL 全失败 = 半完整库 + `alembic_version` 行可能重叠;基本无法可靠恢复。spec §24.2 明确 "停 app → drop db → create db → pg_restore → 起 app"(restore 落空库,起 app 后 entrypoint 看到 alembic_version 已是头版,不再 migrate)。
   - **修复方案 A(推荐)**:让 `postgres` 服务本身做 pg_restore(postgres:16-alpine 自带 `pg_restore`)。给 postgres service 加 `/var/lib/bid-app/backups:/backups:ro` 挂载,然后 `docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" /backups/<dump>`。最后再 `start app`。
   - **修复方案 B**:用 `docker compose run --rm --entrypoint /app/backend/.venv/bin/pg_restore app -h postgres ...` 一次性容器,绕过 entrypoint.sh 的 alembic;再 `start app`。
   - ✅ **解决**(commit `e6b1f49`,采用方案 A):`docker-compose.yml:53` 给 postgres service 加 `/var/lib/bid-app/backups:/backups:ro` 只读挂载;`scripts/restore-backup.sh` 完全重写,顺序变为 ① postgres exec pg_restore --list 校验 → ② stop app(阻 alembic)→ ③ postgres exec drop/create 空库 → ④ postgres exec pg_restore `--clean --if-exists --no-owner --no-privileges --exit-on-error` → ⑤ start app(alembic upgrade head 此时是 no-op)→ ⑥ 等 healthcheck;脚本头注释明确解释了为什么不让 app 容器跑 pg_restore;README.md "灾难恢复"章节同步更新为新流程。`--exit-on-error` 防 partial restore;`--clean --if-exists` 兜底残留对象。

#### ✅ 通过项

- **Dockerfile**:多阶段(frontend-builder + runtime)结构正确;依赖装齐(pandoc / chromium / fonts-noto-cjk + extra / nodejs / npm / supervisor / cron / **postgresql-client-16**);mmdc 11.4.0;mermaid 三个配置文件 COPY 到 `/etc/`;cron 已写 `0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh`;ENTRYPOINT entrypoint.sh + CMD supervisord -n。
- **entrypoint.sh**:顺序符合 D-O — 写 `/etc/bid-app.env`(给 cron) → 等 postgres 60 次 → `alembic upgrade head` 同步 → `exec "$@"` 起 supervisord;不再 `service cron start`(避免与 supervisord 双进程争 pidfile)。
- **supervisord.conf**:`nodaemon=true`;[uvicorn] / [arq-worker] / [program:cron] 三程序;stopwaitsecs 合理(uvicorn 10s / arq-worker 30s);stdout/stderr → /dev/fd/1 配合 docker logs。
- **docker-compose.yml**:bind mount `/var/lib/bid-app/{projects,backups,postgres-data,redis-data}`(NFR-2);`env_file: .env`(D-R 单一文件);postgres 16-alpine + healthcheck `pg_isready` + `init-test-db.sh` 挂到 `/docker-entrypoint-initdb.d/10-init-test-db.sh:ro`(D-DS / D-EA);redis 7-alpine + `--maxmemory-policy noeviction --appendonly yes`(D-V);depends_on `service_healthy`;app healthcheck `/health`。
- **docker-compose.dev.yml**:仅 db + redis(后端 / 前端 / arq 在宿主机跑);本地数据卷 `./.dev-data/` 与生产 `/var/lib/bid-app/` 隔离;init-test-db.sh 同样挂载。
- **init-test-db.sh**(D-DS)+ **scripts/create-test-db.sh**(D-DV):分流明确,init 脚本含"非幂等,空卷首启才执行,已有卷必须手动跑 create-test-db.sh"的注释;create-test-db.sh `SELECT 1 FROM pg_database` 显式幂等检查 + 自动从 `.env` 加载 + macOS / Ubuntu psql 安装提示。
- **scripts/install.sh**:校验 docker / docker compose / python3;EUID!=0 时透明走 sudo;mkdir + chown(`postgres-data` 999:999 / `redis-data` 999:999 / `projects` `backups` 1000:1000);`.env` 已在则跳过;`docker compose build` + `up -d` + 等 healthcheck 5 分钟;末尾 R10 警告。
- **scripts/gen-secrets.sh**:从 `.env.example` `cp` 到 `.env`;**拒绝覆盖已存在 .env**;Python `secrets.token_hex(32)` / `token_urlsafe(24)` 生成;sed 占位符替换 + `chmod 600` + 自检无残留 `__GENERATE_ME__ / __64_HEX_CHARS__`;输出 master_key 前 8 位 + R10 警告。
- **scripts/restore-backup.sh**(M5-FIX `e6b1f49` 后):二次确认(交互式 yes / `FORCE=1`);自动加载 `.env`;`postgres exec pg_restore --list` 预校验;auto-start postgres if down;**正确顺序** stop app → drop/create empty DB → pg_restore (--clean / --if-exists / --exit-on-error) → start app → wait healthcheck;头注释解释为什么不让 app 跑 pg_restore;落地为方案 A。
- **docker/pg-backup.sh**:从 `/etc/bid-app.env` 取环境;`pg_dump -F c` 写 `.partial → mv` 原子提交;7 天滚动 `find -mtime +7 -delete`。
- **.env.example**:R10 警告醒目;包含所有 §6 字段(POSTGRES_* / REDIS_URL / BID_APP_MASTER_KEY / JWT_SECRET / 默认 admin / LLM 模型 / 业务参数 / 路径 / 日志);**不**写 `DATABASE_URL=postgresql+asyncpg://${VAR}/...`(D-W,DSN 由 config.py 拼)。
- **README.md**:一键部署 / 本地开发 / 测试库初始化(D-EA / D-DV 分流表)/ 部署运维 / 备份恢复 / master_key 轮换 / 数据卷布局 / 健康检查 / 升级流程,链接全部 §章节;R10 警告完整 prominent。
- **main.py 启动横幅**(M5-4):lifespan async context manager 调用 `_print_startup_banner()`,纯叠加 — 不修改其它逻辑。banner 含端口 / 默认 admin/admin123 警告 / `BID_APP_MASTER_KEY` sha256 前 16 字节 / R10 提醒;所有 print `flush=True file=sys.stdout`(supervisord stdout_logfile=/dev/fd/1 能转发到 docker logs)。
- **mermaid configs**(`docker/mermaid-config.json` / `puppeteer-config.json` / `mermaid.css`):字体 `Noto Sans CJK SC`;chromium executablePath / `--no-sandbox` 等四个参数;CSS `* { font-family: ...; !important }`。三件套与 §13.2 一致。

#### 🟡 非阻塞建议(留作 nitpick,不发 SendMessage)

- `entrypoint.sh:10-13` 的 `for v in ... BACKUPS_DIR TZ`,`set -u` 下若 `.env` 未含 `BACKUPS_DIR` 则 entrypoint 直接 abort。`.env.example` 已含,正常路径无问题;但若用户精简 `.env` 删了该行,容器死循环。可考虑 `${var:-}` 默认值或 `set +u` 局部包裹。
- `scripts/install.sh:73` 给 `redis-data` chown 999:999 — spec §17.3 line 5677 只说 postgres-data:999。redis:7-alpine 实际也是 uid 999,chown 不会出错;但与 spec 文字不严格一致。建议更新 spec 或保留为防御。
- `docker-compose.yml` 假设 compose 项目根 = `app/`(因为 `.env` 在那里)。如果运维用 `docker compose -f app/docker-compose.yml` 从仓库根跑,compose 会读仓库根 `.env`,`${POSTGRES_PASSWORD}` 等不会被插值,postgres 容器起不来。README.md / install.sh 都已让用户 `cd app/`,正常路径 OK。但可在 README "升级流程"段加一句 `cd app/` 提醒。
- `seed-admin-user.sh` / `seed-test-key.sh`:依赖 `bid_app.models.user.User` / `bid_app.core.crypto.encrypt_api_key`,这些是 M1 / M2 才落地的模块,M5 阶段直接跑会 ImportError。脚本里已有 except 提示,**不算 bug**(forward-compat 给 M1 用)。

### ACCEPTANCE-AUDIT(任务 #37,待全 milestone 完成)

> 等所有里程碑完成后,写 `app/ACCEPTANCE_AUDIT.md` 走查 §23 23 个 checkbox。

---

## Non-blocking review notes(累积)

> 实际审查中发现的 nitpick 累积在此(不通过 SendMessage 发回);每条标注里程碑 + 文件路径 + 简述。

- M5 / `docker/entrypoint.sh:10-13`:`set -u` 下 `BACKUPS_DIR` 未设会让 entrypoint abort。
- M5 / `scripts/install.sh:73`:redis-data chown 999:999 是防御性的(spec 未要求,但与 redis:7-alpine 实际 uid 一致)。
- M5 / `docker-compose.yml`:依赖 compose 项目根 = `app/`,跨目录调用会 break `${VAR}` 插值。
