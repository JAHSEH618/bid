# RUNTIME_TEST_REPORT — 本地启动 + 烟囱测试 + 重启验证

任务 #41 的执行记录。runtime test 在本地 macOS + Colima Linux VM 跑。本文件实时记录每步结果 + 发现的运行时 bug + 修复路径。

## 环境

| 项 | 值 |
|---|---|
| 宿主 OS | macOS Darwin 25.4.0(ARM64) |
| Docker daemon | Colima Linux VM(Ubuntu 24.04.4 LTS,4 CPU / 7.7 GB RAM / 30 GB disk)|
| Docker CLI | 29.3.0(Homebrew) |
| Docker Compose | 5.1.0(plugin) |
| Buildx | v0.33.0 |
| 测试日期 | 2026-05-03 |
| `.env` 模式 | `BID_APP_FAKE_LLM=1`(省真 dashscope) |
| 数据卷 | `./.dev-data/` 本地 bind mount(`docker-compose.override.yml`),无 sudo 跑 |

## 阶段 1 — 环境前置

| 步骤 | 结果 | 备注 |
|---|---|---|
| `colima start --cpu 4 --memory 8 --disk 30` | ✅ pass(< 1 分钟) | daemon 暴露在 `~/.colima/default/docker.sock` |
| `docker compose version` 解析 | ⚠️ 修了 | OrbStack 残留符号链接断,replace 到 `/opt/homebrew/lib/docker/cli-plugins/docker-compose` |
| `docker buildx version` | ⚠️ 修了 | 同上;`brew install docker-buildx` + 替换符号链接 |
| `docker-credential-osxkeychain` 缺失 | ⚠️ 修了 | `~/.docker/config.json` 删 `credsStore: osxkeychain`(本地拉公开镜像无需 keychain) |
| `bash scripts/gen-secrets.sh` | ⚠️ 修了 | 自检误命中 `.env.example` 头部注释里的 `__GENERATE_ME__` 字面量,fix anchor `^[A-Z_]+=`(commit `0d37535`) |
| `.env` 末尾 append `BID_APP_FAKE_LLM=1` | ✅ pass | |

## 阶段 2 — `docker compose build`

| 步骤 | 结果 | 备注 |
|---|---|---|
| frontend builder(node:20-alpine + pnpm) | ✅ pass | dist 产出 |
| runtime stage 系统依赖(pandoc / chromium / mmdc / fonts-noto-cjk + extra / postgresql-client-16) | ✅ pass | apt 装 + pgdg APT 加 |
| `uv sync --frozen --no-dev` | ✅ pass(63s)| 159 packages,bid-app 0.1.0 + 全依赖 |
| `COPY backend/ /app/backend/` 覆盖了 `.venv/` | 🔴 BUG → 修了 | 宿主机 `.venv/bin/python` 符号链接指向 `/Library/Frameworks/.../python3.12`,容器里那个路径不存在,entrypoint 跑 alembic 时 "No such file or directory"。修:加 `app/.dockerignore` 屏蔽 `backend/.venv/`(commit `0d37535`)。重 build 后 `.venv/bin/python` 正确指向 `/usr/local/bin/python3` |

## 阶段 3 — `docker compose up -d`

| 步骤 | 结果 | 备注 |
|---|---|---|
| `bid-postgres` healthy | ✅ pass | < 10s |
| `bid-redis` healthy | ✅ pass | < 5s |
| `bid-app` 等 healthy | 🔴 **BUG**(passlib + bcrypt 5.0)| 容器 restart loop。**已 SendMessage backend-lead**(下方详情)|

### Bug R-1:passlib 1.7.4 + bcrypt 5.0 ABI 不兼容 → migration `0001_initial.py:364` 挂 ✅ FIXED

- **Trigger**:`alembic upgrade head` 跑 `bcrypt.using(rounds=12).hash(pwd)` 写默认 admin
- **Stack**:passlib `detect_wrap_bug` 跑 `bcrypt.hashpw(<200 byte stub>, config)`,bcrypt 5.0.0 严格 reject `> 72 bytes` 抛 ValueError;passlib 1.7.4 期望返回 False 不抛
- **责任**:backend(pyproject.toml 没 pin bcrypt)
- **修复**:backend-lead commit `9d29fd2` 在 pyproject.toml 加 `"bcrypt<5"`,uv lock 解析到 4.3.0

### Bug R-2:Dockerfile uv sync 时机错 → bid_app 包未实质安装 ✅ FIXED

- **Trigger**:R-1 修后 alembic 通过,但 uvicorn 起来时 `ModuleNotFoundError: No module named 'bid_app'`
- **根因**:Dockerfile 原顺序 `COPY pyproject.toml + uv.lock` → `RUN uv sync --frozen --no-dev` → `COPY backend/`。`uv sync` 跑时 src/ 不在,Hatchling editable install 写了空 dist-info(direct_url.json 指向 /app/backend,无 .pth),后续 COPY 不会触发重 sync;site-packages 里 `bid_app-0.1.0.dist-info` 存在但 import 不到
- **修复**:Dockerfile 拆两阶段(commit 待 push)
  - Phase 1:`uv sync --frozen --no-dev --no-install-project`(只装第三方依赖,缓存友好)
  - Phase 2:COPY backend/ 后再跑 `uv sync --frozen --no-dev`(装 bid-app 本身,Hatchling 写 .pth)
- **验证**:`docker run --rm --entrypoint /bin/bash bid-app:latest -c '/app/backend/.venv/bin/python -c "import bid_app; from bid_app.main import app; print(app.title)"'` 不再 ModuleNotFoundError(只在 settings 校验时报缺 env,符合预期)

### Bug R-3:`api/projects.py:557` FastAPI response_model 推断失败 🔄 WAITING backend

- **Trigger**:uvicorn 启动 import `bid_app.main` → `from bid_app.api import projects` → `@router.get("/{project_id}/proposal.md")` 装饰器 `fastapi.utils.create_model_field` raise FastAPIError
- **典型成因**:return annotation 含 `PlainTextResponse | StreamingResponse | Response | Union[..., dict, None]`,FastAPI 想自动生成 pydantic response model 失败
- **修复**:装饰器加 `response_model=None` 显式禁 schema,或去掉 return annotation
- **状态**:已 SendMessage backend-lead;等 push 后 `docker compose build app && up -d --force-recreate app`(arq-worker 当前已 RUNNING,只 uvicorn 死循环)

---

## 阶段 4 — 烟囱测试(待 stage 3 healthy)

待容器全 healthy 后逐项跑:

- [ ] GET / → 前端 SPA index.html(验证 backend SPA fallback,REVIEW-1 🟡 #2)
- [ ] GET /health → 200 JSON(健康检查 db + redis)
- [ ] POST /api/auth/login `admin/admin123` → 200 + cookie + body `must_change_password=true`
- [ ] GET /api/me → 428 PRECONDITION_REQUIRED(改密前)
- [ ] POST /api/me/change-password → 200 + must_change_password=false
- [ ] GET /api/me → 200(改密后)
- [ ] POST /api/projects → 201 + project id
- [ ] POST /api/projects/{id}/documents 上传 .md → 201
- [ ] POST /api/projects/{id}/start → 202(BID_APP_FAKE_LLM=1 模式)
- [ ] GET /api/projects/{id}/stream → SSE 事件流(extracting → outline_ready → ...)
- [ ] LangGraph 跑通到 done(fake LLM mode)→ markdown 出
- [ ] POST /api/projects/{id}/proposal.docx → 202 docx_job_id
- [ ] GET /api/docx-job/{docx_job_id} → 状态 pending → rendering_mermaid → pandoc → finalizing → done
- [ ] GET /api/projects/{id}/proposal.docx → 200 .docx 流(filename 含中文)

---

## 阶段 5 — `docker compose restart` 验证(待 stage 4 通过)

- [ ] in-flight workflow 是否 LangGraph checkpoint 续跑(REVIEW-1 🟡 #3 真验证)
- [ ] SSE 重连
- [ ] 在飞 docx job 是否被 cleanup_stale 收尾或续做

---

## 总览

| 阶段 | 状态 |
|---|---|
| 1 环境前置 | ✅ |
| 2 docker compose build | ✅(修了 .dockerignore) |
| 3 容器 healthy | 🔴 阻塞:passlib + bcrypt 5.0 |
| 4 烟囱测试 | ⏳ 待 stage 3 解阻塞 |
| 5 重启验证 | ⏳ 待 stage 4 通过 |

**修复 commits**(本任务推到 origin/main):
- `0d37535` `.dockerignore` + gen-secrets self-check anchor + override gitignore
