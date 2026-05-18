#!/usr/bin/env bash
# 更新代码后重建并重启线上容器。
#
# 用法:
#   ./scripts/restart-after-update.sh
#   SKIP_BUILD=1 ./scripts/restart-after-update.sh   # 只重启,不重新 build
#
# 必须在 app/ 目录或任意子目录外调用均可;脚本会自动 cd 到 app/。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${BID_APP_DATA_DIR:-/var/lib/bid-app}"
ENV_BACKUP="$DATA_DIR/.env"

cd "$APP_DIR"

SUDO_PREFIX=""
if [[ $EUID -ne 0 ]]; then SUDO_PREFIX="sudo"; fi

docker_cmd=(docker)
# 用 ``docker version`` 而不是 ``docker compose version`` 探测:后者只查
# 插件二进制不连 daemon,非 docker 组用户能通过这条但实际所有 docker 调用
# 都会被 socket 权限拒。
if ! docker version >/dev/null 2>&1; then
  if sudo -n docker version >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  elif sudo docker version >/dev/null 2>&1; then
    # 上一行可能弹密码,弹完进 sudo 缓存,后续都走 sudo
    docker_cmd=(sudo docker)
  else
    echo "❌ docker daemon 不可访问且 sudo 也用不了" >&2
    echo "   把当前用户加 docker 组,或用 sudo 重跑本脚本" >&2
    exit 1
  fi
fi

if [[ ! -f .env ]]; then
  if [[ -f "$ENV_BACKUP" ]]; then
    echo "[restart] app/.env 不存在,从 $ENV_BACKUP 恢复..."
    $SUDO_PREFIX cp "$ENV_BACKUP" .env
    $SUDO_PREFIX chmod 600 .env
    $SUDO_PREFIX chown "$(id -u):$(id -g)" .env 2>/dev/null || true
  else
    echo "❌ app/.env 不存在,且 $ENV_BACKUP 也不存在。先恢复 .env 再重启。" >&2
    exit 1
  fi
fi

if [[ -f "$ENV_BACKUP" ]]; then
  current_master="$(awk -F= '$1=="BID_APP_MASTER_KEY"{print substr($0,index($0,"=")+1)}' .env | tail -1)"
  backup_master="$($SUDO_PREFIX awk -F= '$1=="BID_APP_MASTER_KEY"{print substr($0,index($0,"=")+1)}' "$ENV_BACKUP" | tail -1)"
  if [[ -n "$current_master" && -n "$backup_master" && "$current_master" != "$backup_master" ]]; then
    echo "❌ app/.env 与 $ENV_BACKUP 的 BID_APP_MASTER_KEY 不一致,拒绝重启。" >&2
    echo "   请恢复旧 .env,否则历史 API Key 会无法解密。" >&2
    exit 1
  fi
fi

echo "[restart] 当前 compose 状态:"
"${docker_cmd[@]}" compose ps || true

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "[restart] rebuild app image..."
  "${docker_cmd[@]}" compose build app
fi

echo "[restart] up -d app..."
"${docker_cmd[@]}" compose up -d app

echo "[restart] 等待 /health 通过..."
HEALTHY=0
for i in $(seq 1 60); do
  status="$("${docker_cmd[@]}" inspect --format '{{.State.Health.Status}}' bid-app 2>/dev/null || echo unknown)"
  if [[ "$status" == "healthy" ]]; then
    echo "✅ bid-app healthy (用时 $((i * 5))s)"
    HEALTHY=1
    break
  fi
  if [[ "$status" == "unhealthy" ]]; then
    echo "❌ bid-app healthcheck 失败,最近日志如下:" >&2
    "${docker_cmd[@]}" compose logs --tail 80 app >&2 || true
    exit 1
  fi
  sleep 5
done

if [[ "$HEALTHY" -ne 1 ]]; then
  echo "❌ bid-app 未在 5 分钟内 healthy,最近日志如下:" >&2
  "${docker_cmd[@]}" compose logs --tail 80 app >&2 || true
  exit 1
fi

"${docker_cmd[@]}" compose ps
echo "✅ 重启完成"
