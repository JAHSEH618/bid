"""Auth + me 相关 schemas。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


class MeResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class SetApiKeyRequest(BaseModel):
    key: str = Field(..., min_length=8, max_length=256)


class ApiKeyInfoResponse(BaseModel):
    """API Key 元信息(明文 key 永不返回)。"""

    provider: str
    masked: str  # sk-***xxxx,只露最后 4 位
    last_validated_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


class TokenUsageRow(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int


class TokenUsageSummary(BaseModel):
    user_id: int
    period: str  # e.g. "month" / "all"
    rows: list[TokenUsageRow]
    total_prompt: int
    total_completion: int
