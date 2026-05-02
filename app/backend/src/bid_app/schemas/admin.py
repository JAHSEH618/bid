"""Admin 端 schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminUserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["user", "admin"] = "user"


class AdminUserUpdateRequest(BaseModel):
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None
    reset_password: str | None = Field(None, min_length=8, max_length=128)


class AdminTokenUsageRow(BaseModel):
    user_id: int
    username: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class AdminTokenUsageSummary(BaseModel):
    period: str
    rows: list[AdminTokenUsageRow]
    total_prompt: int
    total_completion: int
