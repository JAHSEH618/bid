#!/usr/bin/env bash
# 显式幂等版测试库创建脚本(D-DV)
# 参考 IMPLEMENTATION_SPEC §17.5
#
# 何时跑:
#   - 本地非 docker 路径跑测试前
#   - 已有 postgres 数据卷(线上 / 已跑过 docker compose up 的开发机):
#     docker entrypoint 不会再补跑 init 脚本,必须手动跑本脚本
#
# 行为:
#   - 用 pg_database 系统表 SELECT WHERE NOT EXISTS 模拟 IF NOT EXISTS
#     (postgres CREATE DATABASE 不支持 IF NOT EXISTS 子句),重复执行不报错
#
# 用法:
#   ./scripts/create-test-db.sh                      # 读 .env / OS env
#   POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
#     POSTGRES_USER=bid_app POSTGRES_PASSWORD=xxx \
#     POSTGRES_DB=bid_app ./scripts/create-test-db.sh
set -e

# 允许从项目根的 .env 自动加载(便于本地非 docker 路径)
if [[ -z "${POSTGRES_USER:-}" && -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi
# 从 app/scripts 子目录跑也支持
if [[ -z "${POSTGRES_USER:-}" && -f ../.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ../.env
  set +a
fi

: "${POSTGRES_USER:?missing POSTGRES_USER (set via .env or env)}"
: "${POSTGRES_PASSWORD:?missing POSTGRES_PASSWORD}"
: "${POSTGRES_DB:?missing POSTGRES_DB}"
: "${POSTGRES_HOST:=localhost}"
: "${POSTGRES_PORT:=5432}"
TEST_DB="${POSTGRES_DB}_test"

export PGPASSWORD="$POSTGRES_PASSWORD"

if ! command -v psql >/dev/null 2>&1; then
  echo "❌ psql 未安装。Mac:brew install libpq && brew link --force libpq;Ubuntu:apt-get install postgresql-client-16" >&2
  exit 1
fi

exists=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
              -d "$POSTGRES_DB" -tAc \
              "SELECT 1 FROM pg_database WHERE datname='${TEST_DB}'")

if [ "$exists" = "1" ]; then
  echo "[create-test-db] ${TEST_DB} 已存在,跳过创建"
  exit 0
fi

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
     -c "CREATE DATABASE \"${TEST_DB}\""
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
     -c "GRANT ALL PRIVILEGES ON DATABASE \"${TEST_DB}\" TO \"${POSTGRES_USER}\""

echo "[create-test-db] ${TEST_DB} 创建完成"
