"""密码与 JWT(§14.2)。

- 密码:bcrypt(rounds=12,与 alembic 0001 默认 admin 一致)
- JWT:HS256,access TTL=2h、refresh TTL=7d,kind 字段强校验
  防止 access token 被当 refresh 用
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from passlib.hash import bcrypt as _bcrypt

from ..config import settings

ACCESS_TTL = timedelta(hours=2)
REFRESH_TTL = timedelta(days=7)


def hash_password(plain: str) -> str:
    return _bcrypt.using(rounds=12).hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.verify(plain, hashed)
    except Exception:
        # passlib 偶尔在 hash 损坏时抛 ValueError,统一返 False
        return False


def _make_token(user_id: int, kind: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "kind": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_access_token(user_id: int) -> str:
    return _make_token(user_id, "access", ACCESS_TTL)


def create_refresh_token(user_id: int) -> str:
    return _make_token(user_id, "refresh", REFRESH_TTL)


def decode_token(token: str, kind: str) -> int:
    """解码 JWT,校验 kind 字段后返回 user_id。"""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("kind") != kind:
        raise jwt.InvalidTokenError("token kind mismatch")
    return int(payload["sub"])
