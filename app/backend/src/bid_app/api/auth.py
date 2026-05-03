"""认证端点(§14.6)。

- ``POST /api/auth/login``  — 用户名密码 → JWT cookie + last_login_at;
  D-Q 失败计数 + 锁
- ``POST /api/auth/logout`` — 删 cookie
- ``GET  /api/auth/me``     — 当前用户(走 lax,允许 must_change_password=true)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.login_throttle import clear_fails, is_locked, record_fail
from ..core.security import (
    create_access_token,
    verify_password,
)
from ..deps import get_current_user_lax, get_db
from ..models import User
from ..schemas.auth import LoginRequest, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=MeResponse)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeResponse:
    """FR-6.7 D-Q:Redis 计数,失败 ≥ 5/min 锁 5 分钟;成功清失败计数。"""
    ip = get_remote_address(request)

    if await is_locked(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多,请 5 分钟后再试",
        )

    user = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        locked_now = await record_fail(ip)
        if locked_now:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录失败次数过多,该 IP 已被锁定 5 分钟",
            )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "用户名或密码错误"
        )

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已禁用")

    await clear_fails(ip)

    # ⭐ R-5 修复:用 Python datetime 而不是 sa.func.now()。
    # sa.func.now() 是 SQL 表达式 token,赋给 ORM attr 后 commit,pydantic
    # 序列化时(响应阶段 session 已关)getattr 触发 lazy refresh →
    # MissingGreenlet。Python datetime 直接是合法 datetime 值,from_attributes
    # 序列化无需 IO。
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    # 显式 model_validate 在 session 还活着时序列化,把 ORM 转 Pydantic DTO。
    # 之后即使 session 关闭,DTO 是纯 Python 对象,不会再触发 lazy load。
    me = MeResponse.model_validate(user)

    response.set_cookie(
        "access_token",
        create_access_token(user.id),
        httponly=True,
        samesite="strict",
        max_age=2 * 3600,
        path="/",
    )
    # ⚠️ refresh_token cookie 暂不下发(REVIEW-2 🟡 #2):
    # 没有 ``/api/auth/refresh`` 端点消费,设它是死数据。后续若加 refresh
    # 流(spec §14.6 未列),在此重新 set_cookie + 配套 logout delete_cookie
    # + ``core.security.create_refresh_token`` 仍保留备用。
    return me


@router.post("/logout")
async def logout(
    response: Response,
    _: Annotated[User, Depends(get_current_user_lax)],
) -> dict[str, bool]:
    """删 cookie。豁免 must_change_password 检查(让首次登录强制改密前
    也能登出)。"""
    response.delete_cookie("access_token", path="/")
    # 兼容老前端:历史会话可能存有 refresh_token cookie,显式 delete 一次
    # 清掉,新 login 已不再下发(见上注释)
    response.delete_cookie("refresh_token", path="/api/auth/refresh")
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[User, Depends(get_current_user_lax)],
) -> MeResponse:
    """⚠️ 走 lax(必须):前端登录后第一时间拉 /me 渲染 UI;若 strict
    走 must_change_password=true 直接 428,前端就拿不到用户信息。

    显式 ``model_validate``(R-5 同源防御):session 关后 ORM 序列化可能
    触发 lazy IO → MissingGreenlet,DTO 是纯 Python 对象 immune。
    """
    return MeResponse.model_validate(user)
