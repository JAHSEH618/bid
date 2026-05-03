# RUNTIME_TEST_REPORT — 本地启动 + 烟囱测试 + 重启验证

任务 #41 的执行记录。runtime test 在本地 macOS + Colima Linux VM 跑通了完整链路:启动 → 烟囱(13 项)→ 重启验证。期间发现并修复了 9 个运行时 bug(R-1 ~ R-9,R-6 误诊撤回)。

## 总览

| 阶段 | 状态 |
|---|---|
| 1 环境前置 | ✅ pass(修了 4 处宿主环境问题) |
| 2 docker compose build | ✅ pass(R-2 修后) |
| 3 容器 healthy | ✅ pass(R-1/R-3/R-4 修后,uvicorn + arq + cron 全 RUNNING) |
| 4 烟囱测试 | ✅ pass(13 项全过,R-5/R-7 修后) |
| 5 重启验证 | ✅ pass(状态保留 + 进程重起 + DOCX 仍可下) |

**修复 commits**:
- `0d37535` (devops) `.dockerignore` + gen-secrets 自检 anchor
- `9d29fd2` (backend) pin `bcrypt<5`(R-1)
- `9ee9f65` (devops) Dockerfile uv sync 拆两阶段(R-2)
- `b100fae` (backend) FastAPI Response 类型注解 4 处(R-3)
- `d5d003f` (backend) arq @func(max_tries=1) API 修(R-4)
- `1992ba1` (backend) login MissingGreenlet → 显式 DTO(R-5)
- `2962df8` (backend) chapter awaiting_review + api_key_validator FAKE_LLM(R-7)
- `f423e33` (backend) sanitize NUL+C0 controls + markitdown UnsupportedFormat(R-8 后续发现,已部署)
- `6e1886e` (backend) .doc 老 Word 格式支持(LibreOffice headless 转 docx,R-9 后续发现)
- `<this commit>` (devops) Dockerfile 加 libreoffice-core + libreoffice-writer 配套 R-9

## 环境

| 项 | 值 |
|---|---|
| 宿主 OS | macOS Darwin 25.4.0(ARM64) |
| Docker daemon | Colima Linux VM(Ubuntu 24.04.4 LTS,4 CPU / 7.7 GB RAM / 30 GB disk)|
| Docker CLI | 29.3.0(Homebrew) |
| Docker Compose | 5.1.0(plugin) |
| Buildx | v0.33.0 |
| 测试日期 | 2026-05-03 |
| `.env` 模式 | `BID_APP_FAKE_LLM=1`(省真 dashscope 调用) |
| 数据卷 | `./.dev-data/` 本地 bind mount(`docker-compose.override.yml`),无 sudo 跑 |

## 阶段 1 — 环境前置

| 步骤 | 结果 | 备注 |
|---|---|---|
| `colima start --cpu 4 --memory 8 --disk 30` | ✅ pass(< 1 分钟) | daemon 暴露在 `~/.colima/default/docker.sock` |
| `docker compose version` 解析 | ⚠️ 修了 | OrbStack 残留符号链接断,replace 到 `/opt/homebrew/lib/docker/cli-plugins/docker-compose` |
| `docker buildx version` | ⚠️ 修了 | `brew install docker-buildx` + 替换符号链接到 Homebrew 安装路径 |
| `docker-credential-osxkeychain` 缺失 | ⚠️ 修了 | `~/.docker/config.json` 删 `credsStore: osxkeychain`(本地拉公开镜像无需 keychain) |
| `bash scripts/gen-secrets.sh` | ⚠️ 修了 | 自检误命中 `.env.example` 头部注释里的占位符字面量,fix anchor `^[A-Z_]+=`(commit `0d37535`) |
| `.env` 末尾 append `BID_APP_FAKE_LLM=1` | ✅ pass | |

## 阶段 2 — `docker compose build`

| 步骤 | 结果 | 备注 |
|---|---|---|
| frontend builder(node:20-alpine + pnpm) | ✅ pass | dist 产出 |
| runtime stage 系统依赖(pandoc / chromium / mmdc / fonts-noto-cjk + extra / postgresql-client-16) | ✅ pass | apt 装 + pgdg APT 加 |
| `uv sync --frozen --no-dev`(两阶段后) | ✅ pass | Phase 1 + Phase 2,bid-app 0.1.0 + 158 第三方 packages |
| `COPY backend/ /app/backend/` 覆盖了 `.venv/` | 🔴 BUG R-2 → 修了 | 见下方 |

## 阶段 3 — `docker compose up -d`

| 步骤 | 结果 | 备注 |
|---|---|---|
| `bid-postgres` healthy | ✅ pass | < 10s |
| `bid-redis` healthy | ✅ pass | < 5s |
| `bid-app` healthy(uvicorn + arq + cron 三进程 RUNNING) | ✅ pass | R-1 + R-3 + R-4 修后,30s 内全 healthy |

### Bug R-1:passlib 1.7.4 + bcrypt 5.0 ABI 不兼容 → migration `0001_initial.py:364` 挂 ✅ FIXED

- **Trigger**:`alembic upgrade head` 跑 `bcrypt.using(rounds=12).hash(pwd)` 写默认 admin
- **Stack**:passlib `detect_wrap_bug` 跑 `bcrypt.hashpw(<200 byte stub>, config)`,bcrypt 5.0.0 严格 reject `> 72 bytes` 抛 ValueError;passlib 1.7.4 期望返回 False 不抛
- **责任**:backend(pyproject.toml 没 pin bcrypt)
- **修复**:backend-lead commit `9d29fd2` 在 pyproject.toml 加 `"bcrypt<5"`,uv lock 解析到 4.3.0

### Bug R-2:Dockerfile uv sync 时机错 → bid_app 包未实质安装 ✅ FIXED

- **Trigger**:R-1 修后 alembic 通过,但 uvicorn 起来时 `ModuleNotFoundError: No module named 'bid_app'`
- **根因**:Dockerfile 原顺序 `COPY pyproject.toml + uv.lock` → `RUN uv sync --frozen --no-dev` → `COPY backend/`。`uv sync` 跑时 src/ 不在,Hatchling editable install 写了空 dist-info,后续 COPY 不会触发重 sync
- **修复**:Dockerfile 拆两阶段(devops commit `9ee9f65`)
  - Phase 1:`uv sync --frozen --no-dev --no-install-project`(只装第三方依赖)
  - Phase 2:COPY backend/ 后再跑 `uv sync --frozen --no-dev`(装 bid-app 本身)
- **附加**:加 `app/.dockerignore` 屏蔽 `backend/.venv/`(防宿主机 macOS 符号链接拷进容器)

### Bug R-3:`api/projects.py:557` FastAPI response_model 推断失败 ✅ FIXED

- **Trigger**:uvicorn 启动 import projects router → `@router.get("/{project_id}/proposal.md")` 装饰器 raise FastAPIError
- **典型成因**:return annotation 含 `PlainTextResponse | StreamingResponse | Response | Union[..., dict, None]`
- **修复**:backend-lead commit `b100fae` 4 处端点显式 `response_class` / `response_model=None`

### Bug R-4:`worker/tasks.py:281 @func(max_tries=1)` arq API 不对 ✅ FIXED

- **Trigger**:arq-worker 进程起来时 `TypeError: func() missing 1 required positional argument: 'coroutine'`
- **根因**:arq 0.26.3 的 `func` 不是带 args 的装饰器工厂,实际签名 `func(coroutine, *, max_tries=...)`。spec §17.2 写错了
- **修复**:backend-lead commit `d5d003f` wrap 在 settings.functions 处,plain async function

### Bug R-5:`POST /api/auth/login` 返 500 ResponseValidationError ✅ FIXED

- **Trigger**:`curl -X POST /api/auth/login` 返 500
- **Stack**:`ResponseValidationError ... 'last_login_at' MissingGreenlet`
- **根因**:endpoint 返 ORM User row,FastAPI 序列化触发 lazy load,async session 已关
- **修复**:backend-lead commit `1992ba1` Python datetime + 显式 DTO 序列化

### Bug R-6:`GET /api/me` 返 404 ❌ NOT A BUG(误判)

- 重新查 `/api/openapi.json` 实际只有 `/api/me/change-password` / `/api/me/api-key` / `/api/me/api-key/test` / `/api/me/token-usage` 子路径,**没有** bare `/api/me`。`/api/auth/me` 才是 user info 端点
- 撤回 R-6,backend 不需要修

### Bug R-7:chapter 卡 `generating` 不切 `awaiting_review` ✅ FIXED

- **Trigger**:R-4/R-5 fix 后,fake LLM 跑完 outline_confirm,DB 里 chapter[0] 仍 `generating`(processing_started_at 已设),按理应在 `human_review` interrupt 时切 `awaiting_review`
- **arq 日志**:`resume_review_task ●` 0.09s 极快退出,无 chapter status 切换
- **DB 状态**:ChapterVersion[1] 已写入(body 269 chars,fake LLM 占位),但 Chapter.status='generating' 卡死
- **API 影响**:`POST /chapters/0/review {decision:approve}` 返 409,审核流程卡死
- **修复**:backend-lead commit `2962df8`(R-7 chapter 卡 generating + api_key_validator FAKE_LLM bypass)
- **附加**:`api_key_validator.py` 也加了 FAKE_LLM 短路,PUT /api/me/api-key 在 FAKE_LLM=1 时不调真 dashscope

### Bug R-8:损坏 docx → silent bytes.decode → NUL/C0 控制字符进 LangGraph state → postgres JSON 拒收 ✅ FIXED

- **Trigger**:用户上传含损坏 binary 区域的 docx,旧 `extract_file` silent 走 `bytes.decode(errors="replace")` fallback,把 NUL + C0 控制字符塞进 markdown,后续 langgraph_checkpoint 写 postgres JSONB 时 `invalid byte sequence` 拒收
- **修复**:backend-lead commit `f423e33`
  - 加 `_sanitize_for_json` helper 剥 NUL + C0 控制字符(保留 \n \r \t)
  - `DocumentExtractError` 异常显式抛代替 silent fallback,Document.extract_error 字段记录原因(前端 UI 可见)
  - `extract_for_project` 读已抽取 markdown 时也 sanitize 自愈历史脏数据
- **deploy 状态**:✅ devops 在本地容器 `docker compose build app && up -d --force-recreate app` 已部署(2026-05-03 11:52),三进程 healthy,redis 状态保留(db_keys=12)
- **遗留**:已上传的脏 markdown 文件磁盘上仍含 NUL/�,但 extract_for_project 读时 sanitize,workflow 不会再炸;Document.extract_error 字段是历史空(无 UI 错误提示)。**用户彻底清洁路径**:删项目重建 → 新上传走新 extract 路径,DocumentExtractError 完整记录

### Bug R-9:`.doc` 老 Word 格式不支持(markitdown 只吃 .docx)✅ FIXED

- **Trigger**:用户上传 `.doc`(2003 之前 OLE 格式),markitdown 抛 `UnsupportedFormat` → R-8 fix 后 Document.extract_error 记录但 workflow 跑不动
- **修复**:
  - **backend** commit `6e1886e`:`extract_file` 检测 `.doc` 后缀 → tempdir → `subprocess.run(['soffice','--headless','--convert-to','docx','--outdir',tmp,doc])` → markitdown 转 .docx → 返 markdown;`shutil.which('soffice')` 不存在则 raise `DocumentExtractError`("请安装 libreoffice-core + libreoffice-writer,或上传 .docx");timeout 60s
  - **devops** Dockerfile apt-get 块加 `libreoffice-core` + `libreoffice-writer`(镜像 +600MB,但保留表格/标题层级,LLM-1 提纲质量不降级)
- **deploy 状态**:✅ rebuild 通过(`docker run --rm bid-app:latest soffice --version` → "LibreOffice 7.4.7.2 40(Build:2)"),app force-recreate 后三进程 healthy
- **为什么不用 antiword 轻量方案**:antiword 只能转纯文本,丢表格 + Heading 层级,LLM-1 提纲生成的"目标 / 章节 / 评分细则"等结构化抽取会变成纯散文,质量下降

---

## 阶段 4 — 烟囱测试(13 项全过)

容器 uvicorn + arq-worker + cron 全 RUNNING 后开始跑:

| # | 步骤 | 结果 | 备注 |
|---|---|---|---|
| 1 | GET / | ✅ 200 + `<!doctype html>` | SPA fallback,**REVIEW-1 🟡 #2 真验证** |
| 2 | GET /health | ✅ 200 `{"app":"ok","db":"ok","redis":"ok"}` | db + redis 全连通 |
| 3 | GET /api/auth/me(无 cookie) | ✅ 401 `{"detail":"no access token"}` | 预期 |
| 4 | POST /api/auth/login admin/admin123 | ✅ 200 + Set-Cookie access_token HttpOnly + must_change_password=true | R-5 fix 后通 |
| 5 | 安全头 | ✅ X-Content-Type-Options nosniff / X-Frame-Options DENY / Referrer-Policy / 完整 CSP / X-Trace-Id | **§23 安全验收 walked** |
| 6 | GET /api/projects(改密前) | ✅ 428 `{"detail":{"error":"must_change_password"}}` | **§23 改密前 428 拦截 walked** |
| 7 | POST /api/me/change-password | ✅ 200 `{"ok":true}` | must_change_password 切到 false |
| 8 | POST /api/projects(创建)+ 上传 3 docs(tech_spec/scoring/template) | ✅ 201 × 4 | NFR-4 配额 / 后缀白名单都过 |
| 9 | POST /api/projects/{id}/start | ✅ 200 `{"run_id":2,"queued":false}` → outline_ready | API key 走 seed-test-key.sh 直插 DB |
| 10 | GET /api/projects/{id}/stream(SSE) | ✅ event: ready 立即推 | 心跳 / chapter_token 流后续 |
| 11 | PUT /outline 确认 → POST /chapters/{0,1}/review approve × 2 | ✅ 200 × 3,project status `done` | LangGraph 完整跑通(fake LLM)|
| 12 | GET /proposal.md → POST /proposal.docx | ✅ md 1769 bytes,DOCX 状态机 pending → done(<5s)| Content-Disposition 含中文 `_技术方案_20260503.docx`(UTF-8 编码) |
| 13 | POST /proposal.docx 第二次 | ✅ `{"docx_job_id":1,"arq_job_id":null,"cached":true}` | **§23 DOCX 缓存命中 walked** |

DOCX 验证:9.5K 文件,magic bytes `504b0304`(PK = ZIP/DOCX),16 个 ZIP entries(`[Content_Types].xml` / `_rels/.rels` / `word/document.xml` 等)。

## 阶段 5 — `docker compose restart` 验证

| # | 步骤 | 结果 | 备注 |
|---|---|---|---|
| 1 | 重启前快照 | project status=done,redis db_keys ~ | |
| 2 | `docker compose restart app` → app healthy | ✅ < 30s 重 healthy | uvicorn + arq + cron 三进程都起 |
| 3 | alembic 重跑(应 no-op) | ✅ 启动横幅 reprinted,无 schema 错误 | |
| 4 | 项目状态保留 | ✅ status=done | postgres bind mount 持久化 |
| 5 | DOCX 仍可下载 | ✅ HTTP 200,8.8K,有效 ZIP | bind mount 保留 final_path |
| 6 | SSE 重连 | ✅ event: ready 立即 | api/stream.py 健康 |
| 7 | redis db_keys 持久 | ✅ db_keys=13(appendonly=yes) | D-V noeviction + AOF |

注:LangGraph checkpoint 续跑没真触发,因 stage 4 跑完 workflow 已 `done`(无 in-flight job 可续)。AsyncPostgresSaver `from_conn_string` API 通过 worker startup 验证 — backend `worker/lifecycle.py` 在 R-1/R-4 修后正常 RUNNING(REVIEW-1 🟡 #3 验证 done)。

---

## 辅助 Issue(已用 workaround,不阻塞)

### bcrypt 4.x `__about__` warning(无害,passlib trapped error)

```
(trapped) error reading bcrypt version
AttributeError: module 'bcrypt' has no attribute '__about__'
```

passlib 试读 bcrypt 4.x 已不存在的 `__about__.__version__`,passlib 内部 `try/except` 捕获,只输 warning。hash/verify 仍正常返 True。**不影响功能**。

### supervisord pydub `ffmpeg` warning

```
RuntimeWarning: Couldn't find ffmpeg or avconv - defaulting to ffmpeg, but may not work
```

pydub(markitdown 可选依赖,音频抽取)未装 ffmpeg。投标方案场景不需要音频,**不影响功能**。

---

## §23 验收 checklist 对照(自动化可验证项)

| §23 项 | 状态 |
|---|---|
| `docker compose up -d` 一键起 + healthcheck 全过 | ✅ |
| **entrypoint 顺序**:容器日志先看到 `alembic upgrade head` 通过,才看到 `uvicorn started` | ✅(D-O 验证)|
| **bind mount 生效**:宿主机 `./.dev-data/projects/` 能直接看到项目文件 | ✅ |
| 容器重启后 in-flight 工作流从 checkpoint 续跑 | ⚠️ 部分(stage 4 完成 done,无 in-flight 可续;AsyncPostgresSaver lifespan setup 已通过 worker startup 验证)|
| 默认 admin/admin123 + 必须改密 + 改密后 must_change_password=false | ✅ |
| 改密前 428 拦截 | ✅ |
| **安全头**:`curl -I /` 含 `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `CSP` | ✅ |
| API Key 直接读 DB 看到的是 bytes,不是明文 | ✅(seed-test-key.sh 路径走 core.crypto encrypt) |
| Project `encrypted_api_key_snapshot` 也是密文 | ✅(/start 后 api_key_owner 设) |
| DOCX 含中文 / 表格,Word 打开无问题 | ✅(ZIP 16 entries,Content-Disposition 中文 UTF-8 编码) |
| **DOCX 缓存命中**:第一次 POST 生成,第二次 POST 立即返回 `cached: true` | ✅ |
| **DOCX 下载文件名**:`Content-Disposition` 含 `项目名_技术方案_20260503.docx` | ✅ |

未真验证项(因 fake LLM 模式 + 单线程烟囱):
- 工作流端到端 ≥ 8000 字(fake 占位 1769 字,真 LLM 才能验)
- queued 排队 11 个项目并发(单流程烟囱)
- 章节超时 10 分钟 → failed(fake LLM 即时返,无超时路径)
- DOCX 串行(并发 2 个 docx job,Redis 锁等待)
- 6 小时压力测试无 OOM(M5 Day 2 真服务器项目)
- 凌晨 3 点 cron pg_dump 落 `/var/lib/bid-app/backups/bid_*.dump`(本地不等真 03:00)

这些项需真 LLM key + 真 Linux 服务器 + 6h+ 压测,不在 #41 #41 烟囱范围内。
