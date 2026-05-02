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

### REVIEW-1(待解锁)

> 等任务 #1 #5 #11 #13 完成。

### REVIEW-2(待解锁)

> 等任务 #18 #22 #23 完成。

### REVIEW-3(待解锁)

> 等任务 #27 完成。

### REVIEW-4(M5 部署)— 2026-05-03

审查范围:M5 commits `2b7e001` / `292e974` / `8e6adaa` / `c80441e`。**§23 整体走查推到任务 #37**(本轮只看 M5 部署交付物的代码 / 配置正确性)。

#### 🔴 严重问题(已 SendMessage devops-lead)

1. **uv.lock 缺失,`docker build` 立刻失败**(`Dockerfile:54` `COPY backend/uv.lock` + `:55` `uv sync --frozen`)
   - **现状**:`app/backend/uv.lock` 不存在,`.gitignore` 也没有忽略它。
   - **影响**:`docker compose build` 在 COPY 阶段直接抛"file not found";`scripts/install.sh` 跑到第 [4/6] 步退出。M5 验收"fresh Linux 30 分钟"无法成立。
   - **修复**:`cd app/backend && uv lock` 生成并 commit `uv.lock`。
   - **责任**:本身是 backend M0 的产物,但 Dockerfile 假设它存在 — devops-lead 与 backend-lead 协作。

2. **`scripts/restore-backup.sh` 灾难恢复顺序错误,落到 alembic 迁移过的 DB → 冲突 / 数据腐蚀**(`scripts/restore-backup.sh:75-94`)
   - **现状**:顺序是 stop app → drop db → create db → **start app(触发 entrypoint:pg_isready + alembic upgrade head + supervisord)** → exec app pg_restore。
   - **问题**:`docker compose start app` 在第 89 行触发 entrypoint.sh 把空数据库做 `alembic upgrade head`,创建全部 schema;紧接着 pg_restore 在 91 行从 dump(custom format,含 `CREATE TABLE / CREATE INDEX / CREATE SEQUENCE` 等)恢复 — 与已存在 schema 全冲突。
   - **影响**:不带 `--clean / --if-exists` 时 `pg_restore` 默认逐对象报错;不进事务时 `COPY` 偶尔成功 + DDL 全失败 = 半完整库 + `alembic_version` 行可能重叠;基本无法可靠恢复。spec §24.2 明确 "停 app → drop db → create db → pg_restore → 起 app"(restore 落空库,起 app 后 entrypoint 看到 alembic_version 已是头版,不再 migrate)。
   - **修复方案 A(推荐)**:让 `postgres` 服务本身做 pg_restore(postgres:16-alpine 自带 `pg_restore`)。给 postgres service 加 `/var/lib/bid-app/backups:/backups:ro` 挂载,然后 `docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" /backups/<dump>`。最后再 `start app`。
   - **修复方案 B**:用 `docker compose run --rm --entrypoint /app/backend/.venv/bin/pg_restore app -h postgres ...` 一次性容器,绕过 entrypoint.sh 的 alembic;再 `start app`。

#### ✅ 通过项

- **Dockerfile**:多阶段(frontend-builder + runtime)结构正确;依赖装齐(pandoc / chromium / fonts-noto-cjk + extra / nodejs / npm / supervisor / cron / **postgresql-client-16**);mmdc 11.4.0;mermaid 三个配置文件 COPY 到 `/etc/`;cron 已写 `0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh`;ENTRYPOINT entrypoint.sh + CMD supervisord -n。
- **entrypoint.sh**:顺序符合 D-O — 写 `/etc/bid-app.env`(给 cron) → 等 postgres 60 次 → `alembic upgrade head` 同步 → `exec "$@"` 起 supervisord;不再 `service cron start`(避免与 supervisord 双进程争 pidfile)。
- **supervisord.conf**:`nodaemon=true`;[uvicorn] / [arq-worker] / [program:cron] 三程序;stopwaitsecs 合理(uvicorn 10s / arq-worker 30s);stdout/stderr → /dev/fd/1 配合 docker logs。
- **docker-compose.yml**:bind mount `/var/lib/bid-app/{projects,backups,postgres-data,redis-data}`(NFR-2);`env_file: .env`(D-R 单一文件);postgres 16-alpine + healthcheck `pg_isready` + `init-test-db.sh` 挂到 `/docker-entrypoint-initdb.d/10-init-test-db.sh:ro`(D-DS / D-EA);redis 7-alpine + `--maxmemory-policy noeviction --appendonly yes`(D-V);depends_on `service_healthy`;app healthcheck `/health`。
- **docker-compose.dev.yml**:仅 db + redis(后端 / 前端 / arq 在宿主机跑);本地数据卷 `./.dev-data/` 与生产 `/var/lib/bid-app/` 隔离;init-test-db.sh 同样挂载。
- **init-test-db.sh**(D-DS)+ **scripts/create-test-db.sh**(D-DV):分流明确,init 脚本含"非幂等,空卷首启才执行,已有卷必须手动跑 create-test-db.sh"的注释;create-test-db.sh `SELECT 1 FROM pg_database` 显式幂等检查 + 自动从 `.env` 加载 + macOS / Ubuntu psql 安装提示。
- **scripts/install.sh**:校验 docker / docker compose / python3;EUID!=0 时透明走 sudo;mkdir + chown(`postgres-data` 999:999 / `redis-data` 999:999 / `projects` `backups` 1000:1000);`.env` 已在则跳过;`docker compose build` + `up -d` + 等 healthcheck 5 分钟;末尾 R10 警告。
- **scripts/gen-secrets.sh**:从 `.env.example` `cp` 到 `.env`;**拒绝覆盖已存在 .env**;Python `secrets.token_hex(32)` / `token_urlsafe(24)` 生成;sed 占位符替换 + `chmod 600` + 自检无残留 `__GENERATE_ME__ / __64_HEX_CHARS__`;输出 master_key 前 8 位 + R10 警告。
- **scripts/restore-backup.sh**:二次确认(交互式 yes / `FORCE=1`);自动加载 `.env`;`pg_restore --list` 预校验 dump 可读;但**核心顺序有 ISSUE-3**(见上)。
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
