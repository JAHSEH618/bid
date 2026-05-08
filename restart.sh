#!/usr/bin/env bash
# 服务器更新代码后,在仓库根执行 ./restart.sh 即可重建并重启 app。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/app/scripts/restart-after-update.sh" "$@"
