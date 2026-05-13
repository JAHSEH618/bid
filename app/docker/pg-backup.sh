#!/usr/bin/env bash
# 由容器内 cron 调用:从 entrypoint.sh 写入的 /etc/bid-app.env 拿环境变量
# 参考 IMPLEMENTATION_SPEC §17.4 / §24.2
# crontab 行(已由 Dockerfile 写入 /etc/cron.d/bid-app-backup):
#   0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1
#
# PR-M7-3 / D2:除 pg_dump 外,同时打包 projects/ 目录(含 blackboard.html
# 与 documents/*.md);恢复脚本配合 --with-files 选项一并展开。
set -euo pipefail

: "${POSTGRES_HOST:?missing}"
: "${POSTGRES_USER:?missing}"
: "${POSTGRES_PASSWORD:?missing}"
: "${POSTGRES_DB:?missing}"
: "${BACKUPS_DIR:?missing}"

TS=$(TZ=Asia/Shanghai date +%Y%m%d_%H%M)
OUT="${BACKUPS_DIR}/bid_${TS}.dump"
TMP="${OUT}.partial"
PROJECTS_DIR="${PROJECTS_DIR:-/var/lib/bid-app/projects}"
FILES_OUT="${BACKUPS_DIR}/projects_${TS}.tar.gz"
FILES_TMP="${FILES_OUT}.partial"

mkdir -p "${BACKUPS_DIR}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -f "${TMP}"

mv "${TMP}" "${OUT}"

# ⭐ PR-M7-3 / D2:同步打包 projects/ 目录(黑板 + 抽取产物)。
# 缺目录不阻塞 (新部署可能尚未创建任何项目)。
if [[ -d "${PROJECTS_DIR}" ]]; then
    tar -czf "${FILES_TMP}" \
        -C "$(dirname "${PROJECTS_DIR}")" \
        "$(basename "${PROJECTS_DIR}")"
    mv "${FILES_TMP}" "${FILES_OUT}"
    echo "[$(TZ=Asia/Shanghai date +%F\ %T)] projects tar ok → ${FILES_OUT}"
else
    echo "[$(TZ=Asia/Shanghai date +%F\ %T)] projects dir missing, skipped tar"
fi

# 滚动:保留最近 7 天 (dump + tar 同步)
find "${BACKUPS_DIR}" -maxdepth 1 -name "bid_*.dump" -mtime +7 -delete
find "${BACKUPS_DIR}" -maxdepth 1 -name "projects_*.tar.gz" -mtime +7 -delete

echo "[$(TZ=Asia/Shanghai date +%F\ %T)] backup ok → ${OUT}"
