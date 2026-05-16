"""Admin 路由(§15)。

全部端点 ``Depends(require_admin)``,role != admin → 403。

- ``GET    /api/admin/users``               列用户
- ``POST   /api/admin/users``               创建用户(must_change_password=true)
- ``PATCH  /api/admin/users/{id}``          改 role / is_active / 重置密码
- ``GET    /api/admin/token-usage``         全局 token 消费汇总

⚠️ **FR-6.5:不提供 DELETE /users/{id}**。Project.created_by / ReviewEvent
/ TokenUsage / ApiKey 都按 user_id 归属历史记录;真删用户会让审计断链或
触发 RESTRICT。"禁用账号"走 ``PATCH {is_active: false}``。
"""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import hash_password
from ..deps import get_db, require_admin
from ..models import User
from ..schemas.admin import (
    AdminTokenUsageRow,
    AdminTokenUsageSummary,
    AdminUserCreateRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ============== Users ==============


@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[User]:
    rows = await db.execute(select(User).order_by(User.id.asc()))
    return list(rows.scalars().all())


@router.post(
    "/users",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: AdminUserCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """创建用户。``must_change_password=true`` 强制首次登录改密。"""
    existing = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"username '{body.username}' already exists")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: int,
    body: AdminUserUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> User:
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    # ⚠️ 防止 admin 把自己降权后无人可用
    if target.id == admin.id and body.role is not None and body.role != "admin":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "cannot demote yourself; ask another admin",
        )

    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.reset_password is not None:
        target.password_hash = hash_password(body.reset_password)
        target.must_change_password = True

    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    _db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
) -> dict[str, bool]:
    """⚠️ FR-6.5:不删账号(保留历史归属)。请用 ``PATCH /users/{id}``
    把 ``is_active`` 设为 false 实现禁用语义。

    端点保留是因为旧前端可能仍在调用;返回 405 让调用方迁移。
    """
    _ = user_id
    raise HTTPException(
        status.HTTP_405_METHOD_NOT_ALLOWED,
        "不支持删除账号(保留历史归属);请改用 PATCH 设 is_active=false 禁用",
    )


# ============== Token usage(全局) ==============


@router.get("/token-usage", response_model=AdminTokenUsageSummary)
async def get_token_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = "month",
) -> AdminTokenUsageSummary:
    if period not in ("month", "all"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "period must be one of: month, all",
        )

    where_clause = ""
    if period == "month":
        where_clause = "WHERE tu.created_at >= date_trunc('month', NOW())"

    rows = (
        (
            await db.execute(
                sa.text(
                    "SELECT tu.user_id, u.username, tu.model, "
                    "SUM(tu.prompt_tokens)::bigint AS p, "
                    "SUM(tu.completion_tokens)::bigint AS c "
                    "FROM token_usage tu "
                    "JOIN users u ON u.id = tu.user_id "
                    f"{where_clause} "
                    "GROUP BY tu.user_id, u.username, tu.model "
                    "ORDER BY tu.user_id, tu.model"
                )
            )
        )
        .mappings()
        .all()
    )

    out = [
        AdminTokenUsageRow(
            user_id=r["user_id"],
            username=r["username"],
            model=r["model"],
            prompt_tokens=int(r["p"] or 0),
            completion_tokens=int(r["c"] or 0),
        )
        for r in rows
    ]
    return AdminTokenUsageSummary(
        period=period,
        rows=out,
        total_prompt=sum(r.prompt_tokens for r in out),
        total_completion=sum(r.completion_tokens for r in out),
    )
