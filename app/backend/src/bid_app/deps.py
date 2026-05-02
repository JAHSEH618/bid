"""FastAPI dependencies — **M1 最小版(D-EC)**。

⚠️ 本文件是 M1 stub:
  - ``get_db``:与最终版一致,直接给 yield AsyncSession
  - ``get_current_user``:dev/test stub。读 ``$BID_APP_DEV_USER_ID`` 或回退到
    ``users`` 表第一个 ``role='admin'`` 行;查不到抛 500 提示先 seed

**M2-3 (#19) 完整版替换**(D-DY,§14.5):接入 JWT cookie / must_change_password
428 / get_current_user_lax / require_admin。M2 起 ``BID_APP_DEV_USER_ID`` 分支
被移除,curl/前端走真登录。

测试:M1 用 ``app.dependency_overrides[get_current_user]`` 注入 fake user;
M1 curl 验收依赖 ``BID_APP_DEV_USER_ID`` 指向 seed admin。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_factory
from .models import User


async def get_db() -> AsyncIterator[AsyncSession]:
    """异步 DB session 依赖(§14.5,M1 最小版与最终版一致)。"""
    async with session_factory() as s:
        yield s


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """⚠️ M1 dev/test stub(D-EC)。

    解析顺序:
      1. ``$BID_APP_DEV_USER_ID`` 数字 ID(指向 seed admin);
      2. 回退查 ``users`` 表第一个 ``role='admin'``、``is_active=true`` 行。

    都查不到抛 500,提示先跑 seed(migration 0001 默认 admin)。

    M2-3 (#19) 用 §14.5 完整 JWT cookie 版本替换;调用方不需做任何兼容,
    签名保持一致。
    """
    raw = os.environ.get("BID_APP_DEV_USER_ID")
    if raw:
        try:
            user_id = int(raw)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"invalid BID_APP_DEV_USER_ID={raw!r} (expect int)",
            ) from e
        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"BID_APP_DEV_USER_ID={user_id} not found in users table; "
                "did you run alembic upgrade head + seed admin?",
            )
        return user

    row = await db.execute(
        select(User)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    )
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "no admin user found; alembic 0001 seeds default admin — "
            "did `alembic upgrade head` run?",
        )
    return user
