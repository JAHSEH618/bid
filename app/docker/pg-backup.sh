#!/usr/bin/env bash
# 由容器内 cron 调用:从 entrypoint.sh 写入的 /etc/bid-app.env 拿环境变量
# 参考 IMPLEMENTATION_SPEC §17.4 / §24.2
# crontab 行(已由 Dockerfile 写入 /etc/cron.d/bid-app-backup):
#   0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1
set -euo pipefail

: "${POSTGRES_HOST:?missing}"
: "${POSTGRES_USER:?missing}"
: "${POSTGRES_PASSWORD:?missing}"
: "${POSTGRES_DB:?missing}"
: "${BACKUPS_DIR:?missing}"

TS=$(TZ=Asia/Shanghai date +%Y%m%d_%H%M)
OUT="${BACKUPS_DIR}/bid_${TS}.dump"
TMP="${OUT}.partial"

mkdir -p "${BACKUPS_DIR}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -f "${TMP}"

mv "${TMP}" "${OUT}"

# 滚动:保留最近 7 天
find "${BACKUPS_DIR}" -maxdepth 1 -name "bid_*.dump" -mtime +7 -delete

echo "[$(TZ=Asia/Shanghai date +%F\ %T)] backup ok → ${OUT}"
