"""Pydantic v2 IO schemas — projects 相关。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = Field(None, max_length=2000)
    pages_per_chapter: int = Field(3, ge=1, le=10)
    max_retry_per_chapter: int = Field(3, ge=0, le=10)


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str | None
    status: str
    created_by: int
    api_key_owner: int | None
    dir_path: str
    pages_per_chapter: int
    max_retry_per_chapter: int
    created_at: datetime

    model_config = {"from_attributes": True}


class StartRequest(BaseModel):
    pages_per_chapter: int = Field(3, ge=1, le=10)
    max_retry_per_chapter: int = Field(3, ge=0, le=10)


class StartResponse(BaseModel):
    run_id: int
    queued: bool


class OutlineChapterIn(BaseModel):
    """提纲编辑端的章节(用户改过的字段)。"""

    id: str | None = None
    title: str = Field(..., min_length=1)
    summary: str | None = None
    key_points: list[str] = Field(..., min_length=1)
    target_pages: int = Field(..., ge=1, le=10)
    matched_scoring_items: list[str] = Field(default_factory=list)


class OutlineConfirmRequest(BaseModel):
    """body.chapters 为空数组 / 缺失 → 自动确认沿用 LLM-1。"""

    chapters: list[OutlineChapterIn] = Field(default_factory=list)


class OutlineChapterOut(BaseModel):
    id: str
    title: str
    summary: str | None
    key_points: list[str]
    target_pages: int


class OutlineResponse(BaseModel):
    project_id: int
    run_id: int | None
    status: str
    chapters: list[dict[str, Any]]


class DocumentUploadResponse(BaseModel):
    id: int
    project_id: int
    kind: str
    original_filename: str
    file_size: int
    extract_error: str | None = None
