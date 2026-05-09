#!/usr/bin/env bash
# M1 dev/test 用:确保 users 表里有一行 role='admin' 的种子用户
# 参考 IMPLEMENTATION_SPEC §22 M1 Day4 / D-EC
#
# 行为:
#   - 调用容器内 Python,使用 passlib bcrypt 哈希 admin 密码
#   - INSERT 一行 User(username=admin, password_hash=..., role='admin', must_change_password=true)
#   - 已存在则跳过(以 username 为唯一键)
#   - 输出该用户 id,可作为 BID_APP_DEV_USER_ID 环境变量值
#
# 用法:
#   ./scripts/seed-admin-user.sh                # 默认 admin / admin123
#   ADMIN_USERNAME=foo ADMIN_PASSWORD=bar ./scripts/seed-admin-user.sh
#
# 前提:bid-app 容器已 up(docker compose up -d 完毕),DB schema 已通过 alembic upgrade head 落地
set -euo pipefail

USERNAME="${ADMIN_USERNAME:-${ADMIN_DEFAULT_USERNAME:-admin}}"
PASSWORD="${ADMIN_PASSWORD:-${ADMIN_DEFAULT_PASSWORD:-admin123}}"

if ! docker compose ps --services --filter status=running | grep -qx app; then
  echo "❌ bid-app 容器未运行,先 docker compose up -d" >&2
  exit 1
fi

docker compose exec -T -e SEED_USERNAME="$USERNAME" -e SEED_PASSWORD="$PASSWORD" \
  app python -c '
import asyncio, os, sys
from sqlalchemy import select
from bid_app.db import session_factory
from bid_app.models.user import User

try:
    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception as e:
    print(f"❌ passlib 不可用:{e}", file=sys.stderr)
    sys.exit(1)

async def main():
    username = os.environ["SEED_USERNAME"]
    password = os.environ["SEED_PASSWORD"]
    async with session_factory() as s:
        existing = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing is not None:
            print(f"[seed-admin] {username} 已存在,id={existing.id}")
            print(f"BID_APP_DEV_USER_ID={existing.id}")
            return
        u = User(
            username=username,
            password_hash=pwd_ctx.hash(password),
            role="admin",
            must_change_password=True,
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        print(f"[seed-admin] 已创建 {username},id={u.id}")
        print(f"BID_APP_DEV_USER_ID={u.id}")

asyncio.run(main())
'
