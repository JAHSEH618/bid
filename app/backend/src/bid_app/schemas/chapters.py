"""Pydantic v2 IO schemas — chapters 相关。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    decision: Literal["approve", "revise", "skip"]
    feedback: str | None = Field(None, max_length=4000)


class ChapterModelUpdateRequest(BaseModel):
    chapter_model: str | None = Field(None, max_length=128)


class ChapterDetailResponse(BaseModel):
    """⭐ R-14 配套:GET /api/projects/{id}/chapters/{idx} 单章详情。

    暴露 ``final_text`` 给前端 hydrate 用——R-14 periodic flush 已经
    保证 ``status='generating'`` 期间也能读到 partial 快照。

    不暴露 ChapterVersion 历史(走另一个端点 / 当前 final_text 已够 P5 渲染)。
    """

    id: int
    index: int
    title: str
    status: Literal[
        "pending",
        "generating",
        "awaiting_review",
        "reviewing",
        "approved",
        "skipped",
        "failed",
        "retrying",
    ]
    final_text: str | None
    chapter_model: str | None = None
    retry_count: int
    last_error: str | None
    current_version_id: int | None  # latest ChapterVersion.id (for /review path)
    updated_at: datetime  # = created_at(没有 onupdate),供前端 cache key
