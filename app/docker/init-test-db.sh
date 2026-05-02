#!/bin/bash
# postgres 镜像首启时自动执行(/docker-entrypoint-initdb.d/ 仅在 PGDATA 数据卷为空时跑)
# 参考 IMPLEMENTATION_SPEC §17.5 D-DS / D-DV
#
# 注意:本脚本本身不是幂等的 — `CREATE DATABASE` 遇到已存在库会报错。
# 仅依赖 docker entrypoint 的"空卷首启"机制保证只跑一次;
# 已有 postgres 数据卷的环境(线上 / 开发机已经跑过 docker compose up)
# 必须手动跑 `scripts/create-test-db.sh`(显式幂等版本)。
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE "${POSTGRES_DB}_test";
    GRANT ALL PRIVILEGES ON DATABASE "${POSTGRES_DB}_test" TO "$POSTGRES_USER";
EOSQL
