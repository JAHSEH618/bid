#!/usr/bin/env bash
# M1 dev/test 用:把测试 dashscope key 加密写入 api_keys 表(归属于 admin user)
# 参考 IMPLEMENTATION_SPEC §22 M1 Day4 / D-EC
#
# 行为:
#   - 调用容器内 Python,使用 BID_APP_MASTER_KEY 走 AES-GCM 加密(与 services/crypto.py 一致)
#   - 取 admin user(role='admin' 第一个),UPSERT 一行 ApiKey(provider='dashscope', encrypted_key=...)
#
# 用法:
#   TEST_API_KEY=sk-xxx ./scripts/seed-test-key.sh
#   或导出 BID_APP_FAKE_LLM=1 时,这一步可跳过(workflow 走 fake LLM)
#
# 前提:容器已 up,seed-admin-user.sh 已执行
set -euo pipefail

if [[ -z "${TEST_API_KEY:-}" ]]; then
  echo "❌ 必须设置 TEST_API_KEY 环境变量" >&2
  echo "   例:TEST_API_KEY=sk-xxx ./scripts/seed-test-key.sh" >&2
  exit 1
fi

if ! docker compose ps --services --filter status=running | grep -qx app; then
  echo "❌ bid-app 容器未运行,先 docker compose up -d" >&2
  exit 1
fi

docker compose exec -T -e SEED_API_KEY="$TEST_API_KEY" \
  app /app/backend/.venv/bin/python -c '
import asyncio, os, sys
from sqlalchemy import select
from bid_app.db import session_factory
from bid_app.models.user import User
from bid_app.models.api_key import ApiKey

try:
    from bid_app.core.crypto import encrypt_api_key
except Exception as e:
    print(f"❌ bid_app.core.crypto.encrypt_api_key 不可用(M2 才落地?):{e}", file=sys.stderr)
    sys.exit(1)

async def main():
    plaintext = os.environ["SEED_API_KEY"]
    encrypted = encrypt_api_key(plaintext)
    async with session_factory() as s:
        admin = (await s.execute(
            select(User).where(User.role == "admin").order_by(User.id).limit(1)
        )).scalar_one_or_none()
        if admin is None:
            print("❌ users 表里没有 admin,先跑 ./scripts/seed-admin-user.sh", file=sys.stderr)
            sys.exit(1)
        existing = (await s.execute(
            select(ApiKey).where(ApiKey.user_id == admin.id, ApiKey.provider == "dashscope")
        )).scalar_one_or_none()
        if existing is not None:
            existing.encrypted_key = encrypted
            await s.commit()
            print(f"[seed-test-key] 已更新 user_id={admin.id} 的 dashscope ApiKey")
        else:
            row = ApiKey(user_id=admin.id, provider="dashscope", encrypted_key=encrypted)
            s.add(row)
            await s.commit()
            print(f"[seed-test-key] 已写入 user_id={admin.id} 的 dashscope ApiKey")

asyncio.run(main())
'
