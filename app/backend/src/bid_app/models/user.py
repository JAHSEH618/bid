"""User 表(§8)。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user|admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ⭐ 用户自定义模型配置(§0002):三类任务各自可选模型,NULL 时走 settings 默认值
    # LiteLLM 格式: "dashscope/qwen3.6-max-preview" 等
    llm1_outline_model: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    llm2_chapter_model: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    llm3_visuals_model: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    model_catalog: Mapped[list[str] | dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
