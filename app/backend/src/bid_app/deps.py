"""FastAPI dependencies — **M2 完整 JWT 版本**(D-EC / §14.5)。

替换 M1 (#6) 的 dev/test stub:
  - ``get_current_user``:从 ``access_token`` cookie 解码 JWT → User
    校验 ``must_change_password`` → 428(D-F);豁免端点用 ``_lax``
  - ``get_current_user_lax``:不检查 ``must_change_password``。
    仅 ``/api/auth/me``、``/api/me/change-password``、``/api/auth/logout`` 用
  - ``require_admin``:user.role 必须 ``admin``,否则 403

⚠️ ``BID_APP_DEV_USER_ID`` 分支已移除(M2 起 curl/前端走真登录)。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .core.security import decode_token
from .db import session_factory
from .models import User


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


async def _resolve_user(request: Request, db: AsyncSession) -> User:
    """从 cookie 解 access_token → User。
    无 token / 解码失败 / 用户不存在或不活跃都抛 401。
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no access token")
    try:
        user_id = decode_token(token, kind="access")
    except Exception as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid token"
        ) from e

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user inactive")
    return user


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """⚠️ 严格版:must_change_password=true 直接抛 428(D-F)。
    豁免端点用 ``get_current_user_lax``。
    """
    user = await _resolve_user(request, db)
    if user.must_change_password:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            detail={"error": "must_change_password"},
        )
    return user


async def get_current_user_lax(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """宽松版:不检查 ``must_change_password``。

    仅以下端点用(避免初次登录用户改密前所有 API 都被 428 锁死):
      - ``GET /api/auth/me``
      - ``POST /api/me/change-password``
      - ``POST /api/auth/logout``
    """
    return await _resolve_user(request, db)


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
