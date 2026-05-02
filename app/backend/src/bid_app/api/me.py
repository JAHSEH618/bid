"""当前用户的自助端点(§15)。

- ``POST   /api/me/change-password``  — 改密(走 lax,首次强制改密路径用)
- ``GET    /api/me/api-key``           — 元信息(masked)
- ``PUT    /api/me/api-key``           — 设置/更新(M2-5 接 validator 后才会
                                         真正测试,本 commit 仅校验 + 加密 + 存)
- ``DELETE /api/me/api-key``           — 删除
- ``GET    /api/me/token-usage``       — 当月 token 消费汇总

⚠️ ``GET /api-key/test`` 走 ``api/me.py`` 但放 M2-5 (#18) 实现(依赖
``services/api_key_validator.py``)。
"""
from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import decrypt_api_key, encrypt_api_key
from ..core.security import hash_password, verify_password
from ..deps import get_current_user, get_current_user_lax, get_db
from ..models import ApiKey, User
from ..schemas.auth import (
    ApiKeyInfoResponse,
    ChangePasswordRequest,
    SetApiKeyRequest,
    TokenUsageRow,
    TokenUsageSummary,
)

router = APIRouter(prefix="/api/me", tags=["me"])
log = structlog.get_logger()


# ============== 改密 ==============


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: Annotated[User, Depends(get_current_user_lax)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """⚠️ 走 lax — 首次登录 must_change_password=true 时也能改密。
    成功后清 ``must_change_password=false``。
    """
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "旧密码错误"
        )
    if body.old_password == body.new_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "新密码不能与旧密码相同"
        )

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await db.commit()
    return {"ok": True}


# ============== API Key CRUD ==============


def _mask(plaintext: str) -> str:
    """sk-xxx → sk-***xxxx(只露最后 4 位,前缀保留首 3 字符)。"""
    if len(plaintext) <= 8:
        return "***"
    return f"{plaintext[:3]}***{plaintext[-4:]}"


@router.get("/api-key", response_model=ApiKeyInfoResponse)
async def get_api_key_info(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyInfoResponse:
    """返回当前用户 ApiKey 元信息(masked,**永不返回明文**)。"""
    api_key = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id, ApiKey.provider == "dashscope"
            )
        )
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "尚未配置 API Key"
        )

    try:
        plaintext = decrypt_api_key(api_key.encrypted_key)
        masked = _mask(plaintext)
    except Exception:
        # 极端情况(master_key 轮换失败 / DB 数据损坏):masked 用 "***"
        log.exception("api_key_decrypt_for_mask_failed", user_id=user.id)
        masked = "***"

    return ApiKeyInfoResponse(
        provider=api_key.provider,
        masked=masked,
        last_validated_at=api_key.last_validated_at,
        created_at=api_key.created_at,
        updated_at=api_key.updated_at,
    )


@router.put("/api-key")
async def set_api_key(
    body: SetApiKeyRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """加密保存 ApiKey。

    ⚠️ M2-4 不调 validator(避免 M2-5 未落时这里报循环 import);
    M2-5 (#18) 在 set 前接 ``api_key_validator.validate_dashscope`` 并
    在失败时 400。本 commit 先存,前端可手动调 ``GET /api-key/test``
    (M2-5)验证。
    """
    encrypted = encrypt_api_key(body.key)
    existing = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id, ApiKey.provider == "dashscope"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.encrypted_key = encrypted
        existing.updated_at = sa.func.now()  # type: ignore[assignment]
    else:
        db.add(
            ApiKey(
                user_id=user.id,
                provider="dashscope",
                encrypted_key=encrypted,
            )
        )
    await db.commit()
    return {"ok": True}


@router.delete("/api-key")
async def delete_api_key(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """删除当前用户的 ApiKey。已启动项目仍能跑(D-C 真快照)。"""
    existing = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id, ApiKey.provider == "dashscope"
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return {"ok": True}  # 幂等
    await db.delete(existing)
    await db.commit()
    return {"ok": True}


# ============== Token usage 查询 ==============


@router.get("/token-usage", response_model=TokenUsageSummary)
async def get_token_usage(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = "month",
) -> TokenUsageSummary:
    """聚合本用户的 token 消费。``period`` ∈ {month, all}。"""
    if period not in ("month", "all"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "period must be one of: month, all",
        )

    where_clause = "WHERE user_id=:u"
    if period == "month":
        where_clause += " AND created_at >= date_trunc('month', NOW())"

    rows = (
        await db.execute(
            sa.text(
                "SELECT model, "
                "SUM(prompt_tokens)::bigint AS p, "
                "SUM(completion_tokens)::bigint AS c "
                f"FROM token_usage {where_clause} "
                "GROUP BY model ORDER BY model"
            ),
            {"u": user.id},
        )
    ).mappings().all()

    out_rows = [
        TokenUsageRow(
            model=r["model"],
            prompt_tokens=int(r["p"] or 0),
            completion_tokens=int(r["c"] or 0),
        )
        for r in rows
    ]
    return TokenUsageSummary(
        user_id=user.id,
        period=period,
        rows=out_rows,
        total_prompt=sum(r.prompt_tokens for r in out_rows),
        total_completion=sum(r.completion_tokens for r in out_rows),
    )
