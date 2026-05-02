#!/usr/bin/env bash
# 灾难恢复:从 pg_dump custom 格式 dump 文件恢复 postgres
# 参考 IMPLEMENTATION_SPEC §24.2
#
# 用法:
#   ./scripts/restore-backup.sh /var/lib/bid-app/backups/bid_20260501_0300.dump
#
# 行为(危险!会丢当前数据):
#   1. 二次确认(--force 或交互式 yes 跳过)
#   2. 停 app 容器(防止恢复期间写入)
#   3. drop & create 数据库(干净重建)
#   4. pg_restore 从 dump 还原
#   5. 起 app 容器
#   6. 触发 healthcheck 验证
set -euo pipefail

DUMP_FILE="${1:-}"
FORCE="${FORCE:-0}"

if [[ -z "$DUMP_FILE" ]]; then
  echo "用法:$0 <dump-file-path>" >&2
  echo "  例:$0 /var/lib/bid-app/backups/bid_20260501_0300.dump" >&2
  echo "  或:FORCE=1 $0 <dump>(跳过交互确认,适合脚本调用)" >&2
  exit 1
fi

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "❌ dump 文件不存在:$DUMP_FILE" >&2
  exit 1
fi

# 加载 .env 拿 POSTGRES_*
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
elif [[ -f ../.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ../.env
  set +a
fi

: "${POSTGRES_USER:?missing POSTGRES_USER (set via .env)}"
: "${POSTGRES_PASSWORD:?missing POSTGRES_PASSWORD}"
: "${POSTGRES_DB:?missing POSTGRES_DB}"

echo "⚠️  即将从 dump 恢复 postgres,当前数据库 ${POSTGRES_DB} 的所有数据将被覆盖!"
echo "   dump file: $DUMP_FILE"
echo "   target db: $POSTGRES_DB"
echo ""

if [[ "$FORCE" != "1" ]]; then
  read -r -p "确认继续?键入 'yes' 继续:" CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    echo "已取消"
    exit 1
  fi
fi

# 1. 验证 dump 文件可读
echo "[restore] 验证 dump 内容..."
docker compose exec -T app pg_restore --list "/var/lib/bid-app/backups/$(basename "$DUMP_FILE")" \
  > /tmp/restore-list.txt 2>&1 || {
    echo "❌ pg_restore --list 失败,dump 文件可能损坏" >&2
    cat /tmp/restore-list.txt >&2
    exit 1
  }
echo "[restore] dump 包含 $(wc -l < /tmp/restore-list.txt) 行(表 / index / 序列)"
rm -f /tmp/restore-list.txt

# 2. 停 app 防写入(保留 postgres + redis)
echo "[restore] 停 app 容器..."
docker compose stop app

# 3. drop & create 数据库
echo "[restore] drop database $POSTGRES_DB..."
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\""
echo "[restore] create database $POSTGRES_DB..."
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$POSTGRES_DB\""

# 4. pg_restore(在 app 容器里跑,因为 app 容器装了 postgresql-client-16,
#    且 backups 目录已 bind mount 到容器内)
echo "[restore] pg_restore..."
DUMP_BASENAME="$(basename "$DUMP_FILE")"
docker compose start app  # 先启 app,因为它有 pg_restore 和 dump 挂载
sleep 5
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" app \
  pg_restore -h "${POSTGRES_HOST:-postgres}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --no-owner --no-privileges \
  "/var/lib/bid-app/backups/$DUMP_BASENAME"

# 5. 重启 app 触发 alembic upgrade head + 服务起来
echo "[restore] 重启 app 容器(触发 entrypoint:alembic upgrade head + supervisord)..."
docker compose restart app

# 6. 等 healthcheck 通过
echo "[restore] 等 healthcheck..."
for i in $(seq 1 60); do
  status=$(docker compose ps --format json app 2>/dev/null | python3 -c '
import json, sys
try:
    arr = json.loads(sys.stdin.read())
    if isinstance(arr, list):
        arr = arr[0] if arr else {}
    print(arr.get("Health", arr.get("State", "unknown")))
except Exception:
    print("unknown")
') || true
  if [[ "$status" == "healthy" ]]; then
    echo "[restore] ✅ app 已 healthy"
    break
  fi
  if [[ "$i" -eq 60 ]]; then
    echo "❌ app 未在 5 分钟内 healthy,检查 docker compose logs app" >&2
    exit 1
  fi
  sleep 5
done

echo ""
echo "✅ 恢复完成"
echo "   curl http://localhost:${APP_PORT:-12123}/health"
