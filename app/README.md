# bid-app — 投标技术方案生成器

基于 LangGraph 的中文投标技术方案自动生成 + 人工审核平台。

- 后端:FastAPI + arq worker + LangGraph + LiteLLM(DashScope)
- 前端:Vite + React + TanStack Query + shadcn/ui
- 部署:单容器(uvicorn + arq + cron 由 supervisord 编排)+ Postgres 16 + Redis 7

详细设计见:
- `REQUIREMENTS.md` — 用户故事 + FR / NFR
- `IMPLEMENTATION_SPEC.md` — 实施规范(单文档,§1-§24,所有决策点 D-A...D-EC)

---

## 一键部署(fresh Linux 服务器)

适用于 Ubuntu 22.04+ / Debian 12+ / CentOS Stream 9 等带 docker engine 的发行版。

```bash
# 1. 装 docker engine(任选其一)
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# 2. clone 仓库
git clone <repo-url> bid && cd bid/app

# 3. 一键部署(创建 /var/lib/bid-app/ 数据目录 + 生成 .env + 起容器 + 等 healthcheck)
sudo ./scripts/install.sh
```

完成后浏览器打开 `http://localhost:12123`,默认 `admin / admin123`(首次登录强制改密)。

> 30 分钟内能跑完(2c4g Ubuntu 22.04 验收口径,§22 M5 Day 2)。

### ⚠️ 必须 `cd app/` 后再跑 docker compose

所有 docker compose 命令必须在 `app/` 目录下运行。**不要**用 `-f` 从仓库根跨目录跑,否则 compose 把仓库根当项目根,读不到 `app/.env`,`${POSTGRES_PASSWORD}` 等被替换成空字符串,postgres 容器起不来:

```bash
# ✅ 正确
cd app/
docker compose up -d

# ❌ 错!compose 项目根 = 仓库根,.env 读不到,postgres 起不来
docker compose -f app/docker-compose.yml up -d
```

`scripts/install.sh` 已强制 cd 到 `app/`;手动跑命令也必须 `cd app/` 再 `docker compose ...`。

---

## 环境变量

`.env.example` 是样板,部署时请运行 `scripts/gen-secrets.sh` 生成 `.env`(自动替换 `__GENERATE_ME__` / `__64_HEX_CHARS__` 占位符为真随机密钥)。

### ⚠️ R10 关键警告:`BID_APP_MASTER_KEY`

- 用 `secrets.token_hex(32)` 生成,固定 64 hex chars(32 字节)。
- **一旦丢失或写错,数据库里所有 `ApiKey.encrypted_key` 永久不可解密**(包括 `Project.encrypted_api_key_snapshot` 真快照),用户必须重新录入 DashScope key,所有进行中的项目报废。
- 部署后启动横幅会打印 sha256 前缀(`docker compose logs app | grep MASTER_KEY`),请与密码管理器里的备份比对确认一致。
- 轮换流程见 `IMPLEMENTATION_SPEC §24.3`(危险操作,需 `--confirm` 显式确认)。

### 启动校验

`config.py` 用 `pydantic-settings` 强校验三个必须密钥:

- `BID_APP_MASTER_KEY` 必须 64 hex chars
- `JWT_SECRET` 必须 64 hex chars
- `POSTGRES_PASSWORD` 必须非空且 `≠ __GENERATE_ME__`

不符合直接 `sys.exit(1)`,容器日志打出明确错误。

---

## 本地开发

后端 / 前端在宿主机直接跑(uv / pnpm),db + redis 用 dev compose 起。

```bash
# 一次性安装
brew install python@3.12 uv node@20 pandoc
corepack enable && corepack prepare pnpm@9.15.0 --activate
npm install -g @mermaid-js/mermaid-cli@11.4.0

# 起 db + redis(数据卷在 ./.dev-data/,与生产 /var/lib/bid-app 隔离)
cd app
./scripts/gen-secrets.sh        # 生成 .env
docker compose -f docker-compose.dev.yml up -d

# 后端
cd backend
uv sync --all-extras
uv run alembic upgrade head

# 终端 A:HTTP server
uv run uvicorn bid_app.main:app --reload --port 12123 --host 127.0.0.1

# 终端 B:arq worker
uv run arq bid_app.worker.settings.WorkerSettings

# 终端 C:前端
cd ../frontend
pnpm install
pnpm dev      # 默认 5173,proxy /api -> 12123
```

---

## 测试库初始化(D-EA / D-DV)

测试库 `${POSTGRES_DB}_test` 与生产/开发库严格分离;`db_engine` fixture 启动校验数据库名必须含 `_test`,避免 `drop_all` 误删开发库。

何时跑哪个脚本:

| 场景 | 命令 | 备注 |
|---|---|---|
| **首次** `docker compose up`(空数据卷) | 自动 | postgres 容器首启时执行 `docker/init-test-db.sh`(挂载到 `/docker-entrypoint-initdb.d/`),无需手动 |
| **已有数据卷的环境**(线上 / 已跑过 compose 的开发机) | `./scripts/create-test-db.sh` | docker entrypoint 不会再补跑 init 脚本(D-DV);本脚本显式幂等(`SELECT 1 FROM pg_database` 检查后 CREATE) |
| **本地非 docker 路径跑测试** | `./scripts/create-test-db.sh` | 同上,自动从 `.env` 加载 `POSTGRES_*` |

> 工程师跑测试如果撞到 `connect refused`,90% 是这个原因。

---

## 部署运维

### 日常命令

```bash
# 看实时日志(JSON 行)
docker compose logs -f app | jq -c .

# 重启某个服务
docker compose restart app

# 查容器状态 + healthcheck
docker compose ps

# 进容器 shell(应急)
docker compose exec app bash
```

### 应急 CLI(§24.1)

```bash
# 重置 admin 密码
docker compose exec app python -m bid_app.cli.reset_admin --password new_pass

# 测试 LLM 连通(M0)
docker compose exec app python -m bid_app.cli.test_llm --api-key sk-xxx
```

### 备份与恢复(§24.2)

容器内 cron 已配置每天凌晨 03:00(`Asia/Shanghai`)自动备份到 `/var/lib/bid-app/backups/bid_YYYYMMDD_HHMM.dump`(滚动保留 7 天)。

**手动备份**:

```bash
docker compose exec app /usr/local/bin/pg-backup.sh
ls -lh /var/lib/bid-app/backups/bid_*.dump
```

**验证 dump 可读**:

```bash
# postgres 容器自带 pg_restore;dump 已挂到 /backups:ro
docker compose exec postgres pg_restore --list /backups/bid_xxx.dump | head
# 应能列出 10 张表 / index / 序列
```

**灾难恢复**:

```bash
# 危险!当前数据库会被覆盖。脚本会二次确认。
./scripts/restore-backup.sh /var/lib/bid-app/backups/bid_20260501_0300.dump
# 内部顺序(关键):
#   1. 停 app(阻止 entrypoint alembic upgrade head 抢先建 schema)
#   2. postgres 容器内 drop & create 空库
#   3. postgres 容器内 pg_restore --clean --if-exists --exit-on-error(空库直恢复)
#   4. 起 app(此时 alembic_version 已是 dump 时版本,upgrade head 是 no-op)
#   5. 等 healthcheck 通过
```

### `BID_APP_MASTER_KEY` 轮换(§24.3)

危险操作,只在 master key 泄漏时做。流程见 `scripts/rotate_master_key.py`(M5+,需 `--confirm` 显式确认):

1. 老 key 解密所有 `ApiKey.encrypted_key` -> 明文
2. 新 key 重新加密 -> 写回 DB(同时刷新 `Project.encrypted_api_key_snapshot`)
3. 改 `.env` 的 `BID_APP_MASTER_KEY`
4. 重启容器

---

## 数据卷布局(NFR-2)

宿主机 `bind mount`,运维直接 `ls` 即可看到内容(不用 named volume,便于备份直接 `cp`):

```
/var/lib/bid-app/
├── postgres-data/       # uid 999:999(postgres 镜像 user)
├── redis-data/          # uid 999:999
├── projects/            # uid 1000:1000(项目附件 / docx 产物)
└── backups/             # uid 1000:1000(每日 pg_dump)
```

`scripts/install.sh` 会自动 mkdir + chown。

---

## 健康检查

容器 `healthcheck` 配置:

- **app**:`curl -fsS http://localhost:12123/health`,interval 30s,start_period 60s,retries 5
- **postgres**:`pg_isready`,interval 5s,retries 6
- **redis**:`redis-cli ping`,interval 5s,retries 6

`docker compose ps` 看 `STATUS` 列含 `(healthy)` 表示一切正常。

---

## 升级流程

```bash
# 1. 备份(以防万一)
docker compose exec app /usr/local/bin/pg-backup.sh

# 2. 拉新代码
git pull

# 3. rebuild 镜像并重启
docker compose build app
docker compose up -d app

# 4. entrypoint.sh 会自动跑 alembic upgrade head(D-O 同步执行)
# 5. 看日志确认 schema 升级成功
docker compose logs --tail 50 app | grep -E "alembic|uvicorn started"
```

如 alembic migration 失败,容器会进入 restart loop,`docker compose logs app` 看具体错误,通常是:

- DB connection refused → postgres 还没起 / `POSTGRES_PASSWORD` 不对
- Migration conflict → `IMPLEMENTATION_SPEC §9` 看是否需要手动修

---

## 相关文档

| 文档 | 内容 |
|---|---|
| `REQUIREMENTS.md` | 用户故事 + FR / NFR + 业务规则 |
| `IMPLEMENTATION_SPEC.md` | 完整实施规范(§1-§24)/ 所有 D-* 决策点 / 风险表 |
| `IMPLEMENTATION_SPEC §22` | 里程碑施工清单(M0 -> M5) |
| `IMPLEMENTATION_SPEC §23` | 验收 Checklist |
| `IMPLEMENTATION_SPEC §24` | 应急 CLI / 备份 / master_key 轮换 / SQL 速查 |
