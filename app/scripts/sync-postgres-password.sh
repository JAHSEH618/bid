#!/usr/bin/env bash
# 应急脚本:把正在运行的 Postgres 角色密码同步为当前 app/.env 里的
# POSTGRES_PASSWORD。
#
# 适用场景:
#   - 服务器保留了 /var/lib/bid-app/postgres-data
#   - 重新部署/重新生成 .env 后,app 日志出现:
#     FATAL: password authentication failed for user "bid_app"
#
# 注意:
#   - 这只修复 POSTGRES_PASSWORD 差异。
#   - BID_APP_MASTER_KEY 如果变了,历史 API Key 仍无法解密;必须恢复旧 .env
#     里的 BID_APP_MASTER_KEY。
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "❌ .env 不存在。请先 cd 到 app/ 并确认 .env 已恢复/生成。" >&2
  exit 1
fi

POSTGRES_USER="$(awk -F= '/^POSTGRES_USER=/{print $2}' .env | tail -1)"
POSTGRES_PASSWORD="$(awk -F= '/^POSTGRES_PASSWORD=/{print $2}' .env | tail -1)"

if [[ -z "${POSTGRES_USER}" || -z "${POSTGRES_PASSWORD}" ]]; then
  echo "❌ .env 缺 POSTGRES_USER 或 POSTGRES_PASSWORD" >&2
  exit 1
fi

DOCKER_CMD=(docker)
if ! docker compose ps >/dev/null 2>&1; then
  if sudo docker compose ps >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
  fi
fi

if ! "${DOCKER_CMD[@]}" compose ps --services --filter status=running | grep -qx postgres; then
  echo "❌ postgres 容器未运行。先执行: docker compose up -d postgres" >&2
  exit 1
fi

"${DOCKER_CMD[@]}" compose exec -T \
  -e PGUSER_TO_SYNC="$POSTGRES_USER" \
  -e PGPASSWORD_TO_SYNC="$POSTGRES_PASSWORD" \
  postgres sh -lc '
set -e
psql -U "$PGUSER_TO_SYNC" -d postgres -v ON_ERROR_STOP=1 \
  -c "ALTER USER \"$PGUSER_TO_SYNC\" WITH PASSWORD '\''$PGPASSWORD_TO_SYNC'\'';"
'

echo "✅ 已把 Postgres 用户 ${POSTGRES_USER} 的密码同步为当前 .env"
echo "   下一步: docker compose restart app && docker compose ps"
