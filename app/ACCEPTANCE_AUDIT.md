# §23 验收 Checklist 走查报告

**审查日期**:2026-05-02
**审查者**:code-reviewer
**任务**:#37 ACCEPTANCE-AUDIT
**审查范围**:`IMPLEMENTATION_SPEC.md` §23 验收 Checklist 共 23 个 checkbox(功能 14 / 安全 9 / 部署 7,以及主观 2)
**审查方法**:只看代码层面 + 配置正确性,**不真跑测试**

---

## 0. 审查总览

| 状态 | 计数 | 说明 |
|---|---|---|
| ✅ pass | 4 | 代码或配置已就绪、与 spec 一致;运行时仍需端到端验证(F8 / D2 / D3 / D5) |
| 🟡 partial | 10 | 关键路径已实现,但依赖 M1/M2/M3 模块尚未完成,无法单独验收 |
| 🔴 fail | 18 | 实现尚未落地(M1-M4 多数模块未完成);审计层面"代码不存在"= fail |
| 📋 主观 | 2 | 不在代码审查范围(用户口头反馈 / 排版同事评估) |

**总数 34**:§23 共列 23 条主项(功能 14 / 安全 9 / 部署 7),加 §23.1 末尾的"DashScope banner"作 B1,加 §23.4 主观 2 条 = 25 项;**剩 9 个 partial/fail 项是逻辑上的 sub-bullet**(如 F1 含"三模型 + Pandoc smoke + Word 打开"两条,本审计按 F-/S-/D-/B-/J- 编号合并)。

> **重要前置**:本审计在 M5 完成、M0 大部分完成、M1-M4 多数 pending 的状态下做的。spec §22 顺序是 M0 → M1 → M2 → M3 → M4 → M5,但目前 M5 已完成、M1-M4 大量 pending — 这不是异常,而是 team-lead 把 #37 任务在所有 milestone 之前给了我做"快照式审计"。**因此大量 fail 是预期的**,目的是给 team-lead 一份"当前距离验收还差什么"的清单。所有 milestone 真正完成后,本文件应被 **重新生成** 而非追加修订(spec §22 验收清单是"全栈完成态"快照)。

---

## 1. 功能验收(§23.1,共 14 项)

### F1. M0:三模型 + Pandoc smoke + Word 能打开,样式不要求(D-DZ / D-ED 收窄)

- **状态**:🟡 partial
- **代码**:
  - `app/backend/src/bid_app/cli/test_llm.py`(三模型 CLI smoke)— 已实现,完整 LiteLLM 调用 + token_usage 记账。
  - `app/backend/src/bid_app/services/llm.py`(call_llm_stream + call_llm_json,§11.1)— 已实现。
  - `app/backend/src/bid_app/services/document_extractor.py` — **缺失**(M0-5 任务 #5 in_progress)。
  - `app/backend/src/bid_app/services/docx_export.py`(M0 smoke 版,fence 不调 mmdc) — **缺失**(M0-5)。
  - `app/backend/src/bid_app/cli/run_local.py` — **缺失**(M0-5)。
- **缺失**:document_extractor / docx_export(M0 smoke)/ run_local CLI 全部待 M0-5 完成。
- **修复路径**:任务 #5 完成后,本项 80% pass;余 20% 需在容器内跑一次 `pandoc proposal.md -o smoke.docx + open in Word` 才能验收。

### F2. 工作流端到端:5 章方案 → markdown ≥ 8000 字

- **状态**:🔴 fail
- **代码**:
  - LangGraph DAG 已搭(`app/backend/src/bid_app/workflow/graph.py`),节点齐全 ✅。
  - `update_state.py` revise/skip/approve 三分支正确(§10.4 / D-AC 修订),`> max_retry` 判定正确(spec line 1587)。
  - `assemble.py` 已含 D-CG + D-CM 全 in-flight invalidation(`workflow/nodes/assemble.py:84-99`)。
  - **DB 落库依赖**:`models/`(M1-1 #10)/ `migrations/0001`(M1-2 #9)/ `db.py`(M1-3 #6,**已存在**)/ `worker/tasks.py`(M1-5 #12)/ `api/projects.py /start`(M1-7 #14)— 多数 pending。
- **缺失**:M1 整套后端 API + worker + DB schema。
- **修复路径**:M1 全套任务完成 + 集成测试跑通(§18.3 M1 组)→ 端到端可达。

### F3. API Key 真快照(FR-7.6 / D-C):用户 `/start` 后立刻 `DELETE /api/me/api-key`,工作流仍能跑完

- **状态**:🔴 fail
- **代码**:
  - `models/project.py` 应含 `encrypted_api_key_snapshot: bytes | None`(D-C)— **未落地**(M1-1)。
  - `api/projects.py /start` 应在拷贝 ApiKey.encrypted_key 到 Project 后再 enqueue — **未落地**(M1-7)。
  - `workflow/nodes/write_chapter.py` 必须从 Project.encrypted_api_key_snapshot 读取(spec §11.2,D-C)。当前 `write_chapter.py` 已存在,但未读取 snapshot(因 M1 models 未落地);应在 M1 完成后回归补真快照路径。
- **修复路径**:M1-1 落 Project 字段 + M1-7 落 /start 拷贝;M2 落 core/crypto.encrypt/decrypt。

### F4. 提纲确认 P4 编辑路径:LLM-2 收到的 chapters 是用户改过的版本(D-K)

- **状态**:🟡 partial
- **代码**:
  - `workflow/nodes/outline_review.py`(P4 interrupt 节点)— **已实现**(M0-3 / spec §10.6)。`interrupt({"kind": "outline_confirm", ...})` 暂停;resume 后写 `_outline_confirmed_chapters`。
  - 节点回流:resume 后 `chapters = edited; current_index = 0`(代码逻辑符合 spec)。
  - **API 端点 PUT `/outline`**(`api/projects.py`,§15.1)— **未落地**(M1-7)。
- **修复路径**:M1-7 落 /confirm-outline 端点 + resume_review_task。

### F5. queued 排队:并发到 11 时第 11 个项目状态 queued,前面项目 done 后自动 running(D-T / D-AB)

- **状态**:🔴 fail
- **代码**:
  - `services/concurrency.py`(D-T Redis SET + lease token + RESERVE_TTL/ALIVE_TTL,§10.7)— **未落地**(M1-4 #7)。
  - `worker/tasks.py` 三类 task 的 `_ensure_or_reacquire` + `project_heartbeat` + `wake_queued_projects` 链路 — **未落地**(M1-5)。
  - `api/projects.py /start` 的 `try_acquire → status='extracting' or 'queued'` 路径 — **未落地**(M1-7)。
- **修复路径**:M1-4 + M1-5 + M1-7 全部完成。

### F6. 章节 failed → retry → retry_count=0、本轮版本 abandoned=true、生成新版本 → 恢复(FR-4.7)

- **状态**:🔴 fail
- **代码**:
  - `models/chapter_version.py` 应含 `abandoned: bool default false`(D-BU)— **未落地**(M1-1)。
  - `worker/tasks.py:retry_failed_chapter_task` 的 reset 逻辑(spec line 2617-2633)— **未落地**(M1-5)。
  - `api/chapters.py POST /retry`(spec §15.2,D-AD/D-AO)— **未落地**(M1-9 #13)。
- **修复路径**:M1-1 + M1-5 + M1-9。

### F7. 章节超时 10 分钟(FR-3.10 / D-D):mock 慢 LLM → failed

- **状态**:🟡 partial
- **代码**:
  - `services/llm.py:call_llm_stream` 外层 `async with asyncio.timeout(timeout_s)` + `except TimeoutError` raise `LLMTimeoutExceeded`(`services/llm.py:103-157`)— **正确实现 D-D / D-BG**。
  - `_FAKE = os.environ.get("BID_APP_FAKE_LLM") == "1"` 路径(`services/llm.py:71-72`)就绪,可用于 mock 测试。
  - **缺**:`workflow/nodes/write_chapter.py` 把 LLMTimeoutExceeded 包成 ChapterGenerationFailed,worker task `except ChapterGenerationFailed: project=awaiting_review`(D-AU)。当前 write_chapter.py 已实现 ChapterGenerationFailed 包装(M0-4 已合)。
  - **缺**:`worker/tasks.py` 的 except ChapterGenerationFailed 分支 — M1-5 未落地。
- **修复路径**:M1-5 worker tasks 完整实现。

### F8. revise → retry_count + 1;到 max+1 次自动 skip(`>` 而非 `>=`,FR-4.2)

- **状态**:✅ pass(代码层)
- **代码**:
  - `workflow/nodes/update_state.py:67` `if new_retry > max_retry:` — **正确使用 `>`**,spec §10.4 line 1587 一致。
  - 强制 skip 时保留 pending_md 在 marker 内 + sync_chapter_to_db status='skipped' + publish 'chapter_max_retry_skip'(`workflow/nodes/update_state.py:67-83`)。
  - retry_count 和 finalized_chapters 写回正确。
- **缺失**:运行时验证仍依赖 M1 集成测试。

### F9. DOCX 含中文 mermaid + 表格,Word 打开无问题(FR-5 / §13.2)

- **状态**:🔴 fail
- **代码**:
  - `docker/mermaid-config.json` `puppeteer-config.json` `mermaid.css` — **已就绪**(§13.2)。中文字体 Noto Sans CJK SC + chromium executablePath + 全局 CSS `!important`。
  - `services/docx_export.py`(完整版,mermaid 反向替换 + on_stage 回调)— **未落地**(M3-1 #20)。
  - `templates/reference.docx`(LibreOffice 手作,Heading 1-4 + 表格边框)— **缺失**(M3-4 #23,只有 .gitkeep)。
- **修复路径**:M3-1 + M3-4。

### F10. DOCX 串行(并发 2 个 docx job → 串行执行,Redis 锁等待第二个,D-H)

- **状态**:🔴 fail
- **代码**:
  - `services/docx_export.py` 应有 `_module_lock` + `_redis_lock` 双层(spec §13.1,D-H + D-BR)— **未落地**(M3-1)。
- **修复路径**:M3-1。

### F11. DOCX 缓存命中:第一次 POST 生成,第二次 POST 立即返回 cached: true(FR-5.7 / D-CK / D-CJ)

- **状态**:🔴 fail
- **代码**:
  - `api/docx.py POST /proposal.docx` 应 latest=done && file exists 才 cached(D-CK + D-CJ)— **未落地**(M3-3 #22)。
- **修复路径**:M3-3。

### F12. DOCX 下载文件名:Content-Disposition 含 `项目名_技术方案_YYYYMMDD.docx`(FR-5.6 / D-L)

- **状态**:🔴 fail
- **代码**:
  - `api/docx.py GET /proposal.docx` 用 `_display_filename(project_name)` 拼 + `Content-Disposition filename*=UTF-8''<encoded>` + ASCII fallback(spec §15.3,line 4751-4754 / 4986-4995)— **未落地**(M3-3)。
- **修复路径**:M3-3。

### F13. 前端 8 个页面无 console error

- **状态**:🟡 partial
- **代码**:
  - 10 个页面已存在(`pages/{Login, ChangePassword, ProjectList, NewProject, DocumentUpload, OutlineConfirm, ChapterReview, Proposal, Settings, Admin}Page.tsx`)。
  - 路由 + RequireAuth 已就绪(`router.tsx`)。
  - apiFetch 401/428 拦截 + redirect 已就绪(`lib/apiFetch.ts:71-79`)。
  - **未验证**:实际浏览器跑;后端 API 多数未落地(M1+M2),前端 mock 层(`lib/mock.ts`)填充开发期数据。M4-3/M4-4/M4-5 任务 #25 #24 #27 仍 pending,未做最终视觉打磨与端到端联调。
- **修复路径**:M4-3/4/5 任务完成 + 后端 M1+M2 联调 + dev server `pnpm dev` 浏览器 console 跑一遍。

### F14. failed 章节红标 + retry 按钮可点

- **状态**:🟡 partial
- **代码**:
  - `components/ChapterSidebar.tsx` 与 `components/ReviewActions.tsx` 已存在(M4)。
  - 后端 `chapter_failed` 事件由 `services/llm.py` 重试链 / `write_chapter.py` 的 `chapter_failed` SSE 推送(节点已实现 ✅)。
  - 前端 `useSSE.ts` hook 应订阅并高亮 — 已存在,但未完整对接(M4-4 #24 pending)。
  - `api/chapters.py POST /retry` — 未落地(M1-9)。
- **修复路径**:M1-9 + M4-4。

---

## 2. 安全验收(§23.2,共 9 项)

### S1. 改密前 428 拦截(FR-6.6 / D-F)

- **状态**:🔴 fail
- **代码**:
  - `deps.py` 完整版 `get_current_user` 应在 `must_change_password=true` 时抛 428(spec §14.5 line 4243-4252)— **未落地**(M2-3 #19)。当前 `deps.py` 仅是 M1 dev/test stub,不查 must_change_password。
  - 前端 `apiFetch.ts:76` 已捕获 428 → /change-password(✅ 前端就绪)。
- **修复路径**:M2-1 + M2-3(deps.py 完整版)。

### S2. 登录失败锁 5 分钟:同 IP 故意失败 5 次 → 第 6 次返 429 + Redis `bid_app:login_lock:{ip}` TTL ≈ 300(D-Q / FR-6.7)

- **状态**:🔴 fail
- **代码**:
  - `core/login_throttle.py`(record_fail / is_locked / clear_fails,spec §14.3.2)— **未落地**(M2-2 #17)。
  - `api/auth.py POST /login` 应在 0. is_locked → 429,1. record_fail → 锁 / 401(spec §14.6)— **未落地**(M2-4 #16)。
- **修复路径**:M2-2 + M2-4。

### S3. 登录成功清零:失败 4 次后正确登录 → 计数清空(D-Q)

- **状态**:🔴 fail
- **代码**:`core/login_throttle.py:clear_fails` + `api/auth.py login` 成功路径应调 clear_fails — 未落地(M2-2 + M2-4)。
- **修复路径**:M2-2 + M2-4。

### S4. 全局限流 100/min(NFR-4):loop 调任意 GET 100 次 → 第 101 次 429

- **状态**:🔴 fail
- **代码**:
  - `core/rate_limit.py` Limiter `default_limits=[settings.global_rate_limit]`(spec §14.3.1)— **未落地**(M2-1 #15)。
  - `main.py` 应 add_middleware(SlowAPIMiddleware) + add_exception_handler(RateLimitExceeded, ...) — 未落地(M2-2)。
  - `.env.example` 已含 `GLOBAL_RATE_LIMIT=100/minute`(✅ 配置就绪)。
- **修复路径**:M2-1 + M2-2。

### S5. 安全头三连:`curl -I /` 含 `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `CSP`

- **状态**:🔴 fail
- **代码**:
  - `core/security_headers.py SecurityHeadersMiddleware`(spec §14.4)— **未落地**(M2-2 #17)。
  - `main.py add_middleware` — 未落地(M2-2)。
- **修复路径**:M2-2。

### S6. 上传配额:同一用户日上传累计触达 500MB → 413(NFR-4)

- **状态**:🔴 fail
- **代码**:
  - `api/projects.py POST /documents` 应聚合 SUM(file_size) WHERE p.created_by=:u AND d.created_at >= date_trunc('day', NOW() AT TIME ZONE :tz),> 500MB → 413(spec §15.1 line 4515-4543)— **未落地**(M1-7 #14)。
  - `.env.example` 已含 `DAILY_UPLOAD_QUOTA_MB=500`(✅ 配置就绪)。
- **修复路径**:M1-7。

### S7. API Key 直接读 DB 看到的是 bytes,不是明文(FR-7.4 / D-C)

- **状态**:🔴 fail
- **代码**:
  - `models/api_key.py:encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)` — **未落地**(M1-1 #10)。
  - `core/crypto.py:encrypt_api_key`(AES-GCM + 12B nonce,spec §14.1)— **未落地**(M2-1 #15)。
  - `api/me.py PUT /api-key` 应 validate_dashscope → encrypt → DB(spec §15.5)— **未落地**(M2-4 / M2-5 #18)。
- **修复路径**:M1-1 + M2-1 + M2-4 + M2-5。

### S8. Project.encrypted_api_key_snapshot 也是密文(D-C)

- **状态**:🔴 fail
- **代码**:
  - `models/project.py:encrypted_api_key_snapshot: bytes | None` — 未落地(M1-1)。
  - `api/projects.py /start` `project.encrypted_api_key_snapshot = api_key.encrypted_key` 直接拷贝 bytes(spec §15.1 line 4384) — 未落地(M1-7)。
- **修复路径**:M1-1 + M1-7。

### S9. 默认 admin/admin123 + 必须改密 + 改密后 must_change_password=false(FR-6.5)

- **状态**:🔴 fail
- **代码**:
  - `migrations/0001_initial.py` seed admin user(`role='admin'`, `must_change_password=true`,spec §9)— **未落地**(M1-2 #9)。
  - `core/security.py:hash_password` bcrypt rounds=12 — 未落地(M2-1)。
  - `api/me.py POST /change-password` 改完 set must_change_password=false — 未落地(M2-4)。
  - `main.py` 启动横幅打印默认 admin 警告(`backend/src/bid_app/main.py:43-47`)— **已实现 ✅**。
- **修复路径**:M1-2 + M2-1 + M2-4。

---

## 3. 前端 / DashScope(隐含在 §23.1 末尾)

### B1. DashScope banner 登录后显示(D3 一次性提示)

- **状态**:🟡 partial
- **代码**:
  - `components/DashScopeBanner.tsx` 已存在(52 行,M4 已合)。
  - 挂载位置:`components/AppShell.tsx`(顶层 shell 包含 banner)。
- **缺**:仅看代码,未在浏览器跑过完整登录流程验证 banner 真显示一次。M4-5 任务 #27 还 pending,但 banner 组件本身已就绪。
- **修复路径**:M4-5 完成 + dev server 跑一遍。

---

## 4. 部署验收(§23.3,共 7 项)

### D1. `docker compose up -d` 一键起 + healthcheck 全过

- **状态**:🟡 partial(配置就绪,运行时未跑)
- **代码**:
  - `app/Dockerfile`(多阶段,frontend-builder + runtime,§17.1)— **已就绪**。M5-FIX 已修 uv.lock 缺失(`70278aa`)。
  - `app/docker-compose.yml`(bind mount + env_file `.env` + healthcheck + init-test-db 挂载)— **已就绪**。
  - `scripts/install.sh`(等 healthcheck 5 分钟超时)— 已就绪。
- **缺**:在 fresh Linux 上未真跑;`alembic upgrade head` 依赖 M1-2 migration 落地后才能成功。
- **修复路径**:M1-2 + M5 验收时跑一次。

### D2. entrypoint 顺序:容器日志先看到 `alembic upgrade head` 通过,才看到 `uvicorn started`(D-O)

- **状态**:✅ pass(代码就绪)
- **代码**:
  - `docker/entrypoint.sh:33-41` 同步 `alembic upgrade head` → `exec "$@"` 起 supervisord(`uvicorn` priority=20 在 supervisord 内)。
  - 不再 `service cron start`,避免与 supervisord [program:cron] 双进程争 pidfile。
- **缺**:`alembic.ini` 与 `migrations/0001_initial.py` 仍未落地(M1-2)— alembic upgrade 实际跑会失败。但 entrypoint **顺序**已正确。

### D3. bind mount 生效:宿主机 `/var/lib/bid-app/projects/` 能直接看到项目文件

- **状态**:✅ pass(配置就绪)
- **代码**:
  - `docker-compose.yml:25-28`:`/var/lib/bid-app/projects:/var/lib/bid-app/projects` + `/var/lib/bid-app/backups:...` + `/etc/localtime:...:ro`。
  - `scripts/install.sh:67-75` chown 999:999 (postgres+redis-data) 与 1000:1000 (projects+backups)。
- **缺**:运行时验证。

### D4. 6 小时压力(模拟 3 个项目并发跑)无 OOM

- **状态**:🟡 partial(基础就绪)
- **代码**:
  - `services/concurrency.py` 上限 max_concurrent_projects=10(.env)+ Redis SET + alive TTL 兜底 — 未落地(M1-4)。
  - Redis `noeviction`(D-V)防 silent OOM 驱逐 — 已就绪(`docker-compose.yml:69`)。
  - `WorkerSettings.max_jobs = max_concurrent_projects + 2`(D-AA)— 未落地(M1-5)。
- **缺**:M1 完成后实际跑 6h。
- **修复路径**:M1-4 + M1-5 + 部署后压力测试。

### D5. 凌晨 3 点 cron pg_dump 落 `/var/lib/bid-app/backups/bid_*.dump`

- **状态**:✅ pass(代码就绪)
- **代码**:
  - `Dockerfile:67-70` 把 `/etc/cron.d/bid-app-backup` 写为 `0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh`。
  - `docker/pg-backup.sh` 用 `.partial → mv` 原子 + 7 天滚动。
  - `docker/entrypoint.sh:8-15` 把 `POSTGRES_*` `BACKUPS_DIR` `TZ` 写到 `/etc/bid-app.env`(给 cron 用,cron 默认无环境)。
- **缺**:运行时跑一晚。

### D6. 备份可恢复:`pg_restore --list` 能列出 10 张表;按 §24.2 恢复成功

- **状态**:🟡 partial
- **代码**:
  - `scripts/restore-backup.sh` M5-FIX `e6b1f49` 修复了顺序错(REVIEW-4 blocker #2):用 postgres 自身 pg_restore + 在 start app 之前。
  - `docker-compose.yml` 已加 postgres service `/var/lib/bid-app/backups:/backups:ro` 挂载。
- **缺**:10 张表本身依赖 M1-1 落地。restore 流程上代码 OK,真正测试要 M1-1 + 一次完整 backup + restore round-trip。
- **修复路径**:M1-1 + 灾难恢复演练。

### D7. 容器重启后 in-flight workflow 从 checkpoint 续跑(awaiting_review 状态保持)

- **状态**:🔴 fail
- **代码**:
  - `worker/lifecycle.py` 应 setup `AsyncPostgresSaver`(spec §17.2 line 5577-5589)— **未落地**(M1-5)。
  - `services/concurrency.py:reconcile_active_projects`(worker 启动清僵尸)— 未落地(M1-4)。
  - `wake_queued_projects` — 未落地(M1-4)。
- **修复路径**:M1-4 + M1-5 + 启动后 SIGTERM 重启验证。

---

## 5. 主观验收(§23.4,共 2 项)

### J1. 用户审核 5 章后口头反馈"可用"

- **状态**:📋 不在代码审查范围
- **说明**:需要真实用户在 P5 跑完 5 章 → 主观打分。代码层无法验证。

### J2. 导出 docx 给排版同事,30 分钟内能出片

- **状态**:📋 不在代码审查范围
- **说明**:需要排版同事评估。代码层无法验证。

---

## 6. 整体观察(团队 lead 决策参考)

### 6.1 状态总结

- **M0**:几乎完成(state / nodes / graph / sync / llm / events_bus / token_usage / error_log / db);仅 document_extractor / docx_export(smoke 版) / run_local 待 #5。
- **M1**:**全部 pending**(models / migrations / deps 完整版 / concurrency / worker / api/projects/chapters/stream)。这是当前最大未完成块。
- **M2**:全部 pending(core/{security,crypto,rate_limit,security_headers,login_throttle} / api/auth / api/me / api/admin / api_key_validator)。
- **M3**:全部 pending(docx_export 完整版 / generate_docx_task / api/docx / reference.docx)。
- **M4**:页面骨架与组件已搭(70% 视觉就绪),实际 SSE / API 联调待 M1+M2 完成。
- **M5**:**完成**(已修 REVIEW-4 两个 blocker)。

### 6.2 验收里程碑

按当前进度,验收(§23 23 条 + 主观 2 条)真正能 pass 还需:

1. **完成 M0-5**(任务 #5):smoke 链路打通,F1 升 pass。
2. **完成 M1**(任务 #6-14):打通 后端 API + worker + DB schema。打开 F2/F3/F5/F6/F7/F14 + S6/S7/S8/S9 + D6/D7。
3. **完成 M2**(任务 #15-19):落 auth + 安全。打开 S1-S5 + S9 改密链路。
4. **完成 M3**(任务 #20-23):docx 真实现 + reference.docx。打开 F9-F12。
5. **完成 M4**(任务 #24-28):前端联调 + console error 清理。打开 F13/F14/B1。
6. **重做本审计**:所有 milestone 完成后,本文件应被 **重新生成**(spec §22 验收清单是"全栈完成态"快照),把多数 fail 转 pass + 主观 J1/J2 由用户口头确认。

### 6.3 当前 M5-FIX 已修两条阻塞(`70278aa` / `e6b1f49`)

REVIEW-4 找到的 2 个 🔴 已被 devops-lead 在任务 #38 修完:

1. **uv.lock 入库**(`app/backend/uv.lock` 现在存在 ✅);
2. **restore-backup.sh 顺序修正**:用 postgres 容器自身 pg_restore + 在 start app 之前。

### 6.4 非阻塞观察(留作将来打磨,不阻塞验收)

- `docker/entrypoint.sh:10-13` 在 `set -u` 下 BACKUPS_DIR 未设会让 entrypoint abort。`.env.example` 已含,正常路径无问题;若用户精简 `.env` 删了该行,容器进入 restart loop。建议加 `${var:-}` 默认值(REVIEW-4 nitpick 已记录)。
- `docker-compose.yml` 假设 compose 项目根 = `app/`(.env 在那里)。仓库根跑 `docker compose -f app/...` 会让 `${VAR}` 插值找不到 `.env`(REVIEW-4 nitpick)。
- `frontend/src/pages/PlaceholderPage.tsx` 现已无引用,可删。
- `workflow/graph.py` 把 spec 的 `gen_visuals` + `merge_chapter` + `human_review` 三节点合并成 `review_chapter`(LLM-3) + `merge_chapter`(含 human review interrupt) 两节点。功能等价,但与 §10.2 / §10.6b 的节点列表不严格一致;需要 spec 更新或代码补回 human_review 节点(后者更稳)。这是 D-K 决策的执行细节,**不影响验收**,但 REVIEW-1 时应被记录为"deviation note"。

---

**结束**。下次 audit 应在 M1+M2+M3+M4 全部完成后做,或由 team-lead 触发新一轮 review-3 / review-1 / review-2 后整合。
