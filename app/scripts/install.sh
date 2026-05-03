#!/usr/bin/env bash
# bid-app 一键部署脚本(fresh Linux 服务器,2c4g Ubuntu 22.04 验收口径)
# 参考 IMPLEMENTATION_SPEC §17.3 §22 M5 Day2
#
# 行为:
#   1. 校验前提:docker / docker compose / python3 已装
#   2. 创建宿主机数据目录并 chown 正确(postgres uid=999,业务目录 1000)
#   3. 若 .env 不存在,跑 gen-secrets.sh 生成
#   4. docker compose build + up -d
#   5. 等待 app healthcheck 通过(最长 5 分钟)
#   6. 打印下一步指引
#
# 用法:
#   sudo ./scripts/install.sh                   # 默认动作
#   sudo SKIP_BUILD=1 ./scripts/install.sh      # 跳过 build(已 pull 好镜像)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${BID_APP_DATA_DIR:-/var/lib/bid-app}"

cd "$APP_DIR"

echo "════════════════════════════════════════════════════════════════"
echo "   bid-app 部署脚本"
echo "   APP_DIR:  $APP_DIR"
echo "   DATA_DIR: $DATA_DIR"
echo "════════════════════════════════════════════════════════════════"
echo ""

# 1. 校验前提
echo "[1/6] 校验前提..."

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker 未安装。Ubuntu:" >&2
  echo "   curl -fsSL https://get.docker.com | sh" >&2
  echo "   sudo systemctl enable --now docker" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "❌ docker compose v2 未安装(需要 docker compose 子命令而不是 docker-compose)" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 未安装(gen-secrets.sh 需要)" >&2
  echo "   Ubuntu: apt-get install python3" >&2
  exit 1
fi

# root / sudo(创建 /var/lib/bid-app 需要)
if [[ $EUID -ne 0 ]]; then
  echo "⚠️  当前非 root 用户,创建 $DATA_DIR 需要 sudo 权限" >&2
  echo "   建议:sudo $0" >&2
  echo "   继续中将通过 sudo 调用 mkdir / chown" >&2
fi

echo "    ✅ docker / docker compose / python3 已就绪"
echo ""

# 2. 创建宿主机数据目录
echo "[2/6] 准备 $DATA_DIR..."
SUDO_PREFIX=""
if [[ $EUID -ne 0 ]]; then SUDO_PREFIX="sudo"; fi

$SUDO_PREFIX mkdir -p "$DATA_DIR/postgres-data" "$DATA_DIR/redis-data" \
                     "$DATA_DIR/projects" "$DATA_DIR/backups"

# postgres-data 必须属于 uid=999(postgres:16-alpine 镜像 user)
$SUDO_PREFIX chown -R 999:999 "$DATA_DIR/postgres-data"
# redis-data uid=999:实测 redis:7-alpine 镜像里 redis 用户也是 uid 999
# (各自 distro 默认值,与 postgres 镜像同号属巧合,不耦合)。chown 是 best-effort:
#   - 当前 redis:7-alpine:命中 999,redis 进程直接读写没问题
#   - 将来 redis 升级改 uid:进程会用镜像内实际 user 起,数据卷可能落到 root,
#     重跑 install.sh 也会被 chown 覆盖;无 silent breakage 风险
# spec §17.3 line 5677 只显式提了 postgres-data:999;这里给 redis-data 也写
# 999:999 作前置防御(REVIEW-4 🟡 nit 已注释)。
$SUDO_PREFIX chown -R 999:999 "$DATA_DIR/redis-data"
# 业务目录 1000(应用容器内 user;若用 root 跑则 0:0 也 OK,但保留 1000 兼容)
$SUDO_PREFIX chown -R 1000:1000 "$DATA_DIR/projects" "$DATA_DIR/backups"

echo "    ✅ 数据目录已就绪"
echo ""

# 3. .env(若不存在则生成)
echo "[3/6] 检查 .env..."
if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    echo "❌ .env.example 缺失,无法生成 .env" >&2
    exit 1
  fi
  echo "    .env 不存在,跑 gen-secrets.sh 生成..."
  ./scripts/gen-secrets.sh
else
  echo "    ✅ .env 已存在,跳过(若需重置:mv .env .env.bak && ./scripts/gen-secrets.sh)"
fi
echo ""

# 4. build & up
echo "[4/6] docker compose build & up..."
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  docker compose build
fi
docker compose up -d

echo ""
echo "[5/6] 等 app healthcheck 通过(最长 5 分钟)..."
HEALTHY=0
for i in $(seq 1 60); do
  status=$(docker inspect --format '{{.State.Health.Status}}' bid-app 2>/dev/null || echo "unknown")
  if [[ "$status" == "healthy" ]]; then
    echo "    ✅ app 已 healthy(用时 $((i*5))s)"
    HEALTHY=1
    break
  fi
  if [[ "$status" == "unhealthy" ]]; then
    echo "❌ app healthcheck 失败,docker compose logs app 排查" >&2
    docker compose logs --tail 50 app >&2 || true
    exit 1
  fi
  sleep 5
done

if [[ "$HEALTHY" -ne 1 ]]; then
  echo "❌ app 未在 5 分钟内 healthy,docker compose logs app 排查" >&2
  exit 1
fi
echo ""

# 6. 验证 + 下一步指引
echo "[6/6] 验证 ..."

APP_PORT="$(grep -E '^APP_PORT=' .env | cut -d= -f2 | head -1)"
APP_PORT="${APP_PORT:-12123}"

if curl -fsS "http://localhost:${APP_PORT}/health" >/dev/null; then
  echo "    ✅ /health OK"
else
  echo "    ⚠️  /health 探测失败(可能仍在初始化,稍后 docker compose logs app)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "   ✅ 部署完成"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "下一步:"
echo "  1. 浏览器访问 http://localhost:${APP_PORT}"
echo "  2. 用默认 admin / admin123 登录(首次会强制改密)"
echo "  3. 在设置页录入 DashScope API Key"
echo ""
echo "运维:"
echo "  日志:    docker compose logs -f app | jq -c ."
echo "  备份:    docker compose exec app /usr/local/bin/pg-backup.sh"
echo "  恢复:    ./scripts/restore-backup.sh ${DATA_DIR}/backups/bid_xxxx.dump"
echo "  停服:    docker compose down"
echo ""
echo "⚠️  R10 警告:.env 里的 BID_APP_MASTER_KEY 一旦丢失,所有 ApiKey 永久不可解密"
echo "    强烈建议密码管理器留备份"
