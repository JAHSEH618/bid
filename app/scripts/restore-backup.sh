#!/usr/bin/env bash
# 灾难恢复:从 pg_dump custom 格式 dump 文件恢复 postgres
# 参考 IMPLEMENTATION_SPEC §24.2
#
# 用法:
#   ./scripts/restore-backup.sh /var/lib/bid-app/backups/bid_20260501_0300.dump
#   FORCE=1 ./scripts/restore-backup.sh <dump>   # 跳过交互式 yes 确认
#   ./scripts/restore-backup.sh <dump> --with-files  # PR-M7-3:同时展开
#                                                    # projects/ tar 包
#
# 顺序(关键 — 反过来会撞 alembic 已建的 schema):
#   1. 校验 dump 文件可读(在 postgres 容器里 pg_restore --list)
#   2. **停 app 容器**(防止恢复期间写入,且不让 entrypoint alembic upgrade 先把 schema 建好)
#   3. 在 postgres 容器内 drop & create 数据库(干净空库)
#   4. 在 postgres 容器内 pg_restore(--clean --if-exists 兜底,即使数据卷里有残留对象也能恢复)
#   5. (可选) --with-files:tar -xzf projects_*.tar.gz 覆盖 /var/lib/bid-app/projects/
#   6. **start app**(此时 alembic_version 表已是 head 版,entrypoint 跑 alembic upgrade head 是 no-op;无 schema 冲突)
#   7. 等 healthcheck 通过
#
# 为什么不让 app 容器跑 pg_restore:
#   docker compose start app 会触发 entrypoint.sh,顺序是 pg_isready → alembic upgrade head → exec supervisord;
#   等 app 起来再 docker compose exec app pg_restore,目标库已经被 alembic 建好全套表 + alembic_version 行,
#   pg_restore 默认逐对象 CREATE 全部撞 already exists,半完整恢复 = 数据腐蚀。
#   方案 A(本脚本采用):postgres:16-alpine 自带 pg_restore,backups 目录已挂到 /backups:ro;
#   stop app → postgres exec drop/create + pg_restore(空库)→ start app(alembic upgrade head 是 no-op)。
set -euo pipefail

DUMP_FILE=""
WITH_FILES=0
FORCE="${FORCE:-0}"

for arg in "$@"; do
  case "$arg" in
    --with-files)
      WITH_FILES=1
      ;;
    -h|--help)
      cat <<EOF
用法:$0 <dump-file-path> [--with-files]
  例:$0 /var/lib/bid-app/backups/bid_20260501_0300.dump
  或:$0 <dump> --with-files       (PR-M7-3:同步覆盖 /var/lib/bid-app/projects/)
  或:FORCE=1 $0 <dump>            (跳过交互确认,适合脚本调用)
EOF
      exit 0
      ;;
    *)
      if [[ -z "$DUMP_FILE" ]]; then
        DUMP_FILE="$arg"
      else
        echo "❌ 未知参数:$arg" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$DUMP_FILE" ]]; then
  echo "用法:$0 <dump-file-path> [--with-files]" >&2
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

DUMP_BASENAME="$(basename "$DUMP_FILE")"
# postgres 容器内挂载点是 /backups(docker-compose.yml 已配 /var/lib/bid-app/backups:/backups:ro)
DUMP_IN_PG="/backups/${DUMP_BASENAME}"

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

# 0. 校验 postgres 容器在跑(不在则起来,因为 pg_restore 在它里面跑)
if ! docker compose ps --services --filter status=running | grep -qx postgres; then
  echo "[restore] postgres 容器未运行,启动..."
  docker compose start postgres
  for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -q; then
      break
    fi
    sleep 2
    if [[ "$i" -eq 30 ]]; then
      echo "❌ postgres 60s 内未就绪" >&2
      exit 1
    fi
  done
fi

# 1. 校验 dump 文件可读
echo "[restore] 校验 dump 内容..."
if ! docker compose exec -T postgres pg_restore --list "$DUMP_IN_PG" \
      > /tmp/restore-list.txt 2>&1; then
  echo "❌ pg_restore --list 失败,dump 文件可能损坏或路径不对" >&2
  cat /tmp/restore-list.txt >&2
  rm -f /tmp/restore-list.txt
  exit 1
fi
LINES=$(wc -l < /tmp/restore-list.txt)
echo "[restore] dump 包含 ${LINES} 行(表 / index / 序列)"
rm -f /tmp/restore-list.txt

# 2. 停 app 防写入,且阻止 entrypoint alembic upgrade head 在恢复前先跑
echo "[restore] 停 app 容器(阻止 alembic upgrade head 抢先建 schema)..."
docker compose stop app

# 3. 在 postgres 容器内 drop & create 空数据库
echo "[restore] drop database $POSTGRES_DB..."
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d postgres \
       -v ON_ERROR_STOP=1 \
       -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\""

echo "[restore] create database $POSTGRES_DB..."
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d postgres \
       -v ON_ERROR_STOP=1 \
       -c "CREATE DATABASE \"$POSTGRES_DB\""

# 4. pg_restore(在 postgres 容器内;空库 + --clean --if-exists 兜底)
#    --clean --if-exists:即使空库,残留 ROLE / SCHEMA 等也能 DROP IF EXISTS 再 CREATE
#    --no-owner / --no-privileges:不还原 owner ACL,统一用容器内 POSTGRES_USER
#    --exit-on-error:任一对象失败立即停,避免半完整恢复
echo "[restore] pg_restore..."
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  pg_restore \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --clean --if-exists \
    --no-owner --no-privileges \
    --exit-on-error \
    "$DUMP_IN_PG"

echo "[restore] pg_restore 完成"

# 5. (可选) PR-M7-3:同步覆盖 /var/lib/bid-app/projects/
if [[ "$WITH_FILES" == "1" ]]; then
  # 推算 tar 路径:dump 是 bid_YYYYMMDD_HHMM.dump → tar 是 projects_YYYYMMDD_HHMM.tar.gz
  DUMP_BASE_NO_EXT="${DUMP_BASENAME%.dump}"
  TS_PART="${DUMP_BASE_NO_EXT#bid_}"
  TAR_BASENAME="projects_${TS_PART}.tar.gz"
  TAR_PATH="$(dirname "$DUMP_FILE")/${TAR_BASENAME}"

  if [[ ! -f "$TAR_PATH" ]]; then
    echo "❌ --with-files 找不到对应 projects tar:$TAR_PATH" >&2
    echo "   (PR-M7-3 后期备份才会生成 projects_*.tar.gz)" >&2
    exit 1
  fi

  PROJECTS_DIR="${PROJECTS_DIR:-/var/lib/bid-app/projects}"
  echo ""
  echo "⚠️  --with-files:即将用 ${TAR_BASENAME} 覆盖 ${PROJECTS_DIR}/"
  echo "   现有目录会被清空再展开,操作不可逆。"

  if [[ "$FORCE" != "1" ]]; then
    read -r -p "确认继续?键入 'yes' 继续:" CONFIRM_FILES
    if [[ "$CONFIRM_FILES" != "yes" ]]; then
      echo "已取消 --with-files,仅 DB 已恢复"
      WITH_FILES=0
    fi
  fi

  if [[ "$WITH_FILES" == "1" ]]; then
    PROJECTS_PARENT="$(dirname "$PROJECTS_DIR")"
    PROJECTS_BASENAME="$(basename "$PROJECTS_DIR")"
    echo "[restore] clearing ${PROJECTS_DIR}..."
    rm -rf "${PROJECTS_DIR:?}"
    echo "[restore] extracting ${TAR_BASENAME} → ${PROJECTS_PARENT}/"
    tar -xzf "$TAR_PATH" -C "$PROJECTS_PARENT/"
    if [[ ! -d "${PROJECTS_PARENT}/${PROJECTS_BASENAME}" ]]; then
      echo "❌ 展开后未找到 ${PROJECTS_PARENT}/${PROJECTS_BASENAME}" >&2
      exit 1
    fi
    echo "[restore] projects/ 已覆盖"
  fi
fi

# 6. 起 app(此时 alembic_version 已是 dump 时的版本;entrypoint alembic upgrade head 是 no-op
#    或前向 migration,无 schema 冲突)
echo "[restore] 启动 app 容器(entrypoint 会跑 alembic upgrade head,正常情况是 no-op)..."
docker compose start app

# 6. 等 healthcheck 通过
echo "[restore] 等 healthcheck..."
HEALTHY=0
for i in $(seq 1 60); do
  status=$(docker inspect --format '{{.State.Health.Status}}' bid-app 2>/dev/null || echo "unknown")
  if [[ "$status" == "healthy" ]]; then
    echo "[restore] ✅ app 已 healthy(用时 $((i*5))s)"
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
echo "✅ 恢复完成"
echo "   curl http://localhost:${APP_PORT:-12123}/health"
