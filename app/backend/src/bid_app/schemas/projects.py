"""Pydantic v2 IO schemas — projects 相关。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ⭐ M1 contract:Project.status 全集(单一来源,API 收紧用)
# init / queued:刚建项目还没 /start
# extracting / outlining:/start 后 LangGraph 阶段
# outline_ready:LLM-1 跑完,等用户在 P4 编辑提纲并 /confirm-outline
# running / awaiting_review:章节循环阶段(P5)
# done / failed / aborted:终态
ProjectStatus = Literal[
    "init",
    "queued",
    "extracting",
    "outlining",
    "outline_ready",
    "running",
    "awaiting_review",
    "done",
    "failed",
    "aborted",
]

# Chapter.status 已在 schemas/chapters.py 收紧;outline 端点回填 chapter 嵌入信息
# 时这里复用一份(避免循环 import)
ChapterStatusEnum = Literal[
    "pending",
    "generating",
    "awaiting_review",
    "reviewing",
    "approved",
    "skipped",
    "failed",
    "retrying",
]


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = Field(None, max_length=2000)
    pages_per_chapter: int = Field(3, ge=1, le=10)
    max_retry_per_chapter: int = Field(3, ge=0, le=10)


class ProjectResponse(BaseModel):
    """⭐ M1 任务 #4:**不**暴露 ``dir_path`` / ``api_key_owner`` 给前端。

    - ``dir_path``:服务器端文件路径(``/data/projects/<id>`` 之类),纯
      实现细节,前端拿了也用不上,反而泄露内部目录结构(让外部扫描者
      推断容器内 layout)。需要内部用(下载 / DOCX 路径拼接)的代码继续
      从 ORM ``Project.dir_path`` 取,**不**经过 schema。
    - ``api_key_owner``:用户内部 ID。SSE 团队共享池设计下,其他成员
      看见"项目正在跑"就够了,具体是谁的 API key 在掏配额是结算 / 后台
      统计层面的事(/api/admin),不应在普通 P2 列表 / P3 详情 expose。
    """

    id: int
    name: str
    description: str | None
    status: ProjectStatus
    created_by: int
    pages_per_chapter: int
    max_retry_per_chapter: int
    created_at: datetime

    model_config = {"from_attributes": True}


class StartRequest(BaseModel):
    pages_per_chapter: int = Field(3, ge=1, le=10)
    max_retry_per_chapter: int = Field(3, ge=0, le=10)
    outline_model: str | None = Field(None, max_length=128)
    chapter_model: str | None = Field(None, max_length=128)
    visuals_model: str | None = Field(None, max_length=128)


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
    chapter_model: str | None = Field(None, max_length=128)


class OutlineConfirmRequest(BaseModel):
    """body.chapters 为空数组 / 缺失 → 自动确认沿用 LLM-1。"""

    chapters: list[OutlineChapterIn] = Field(default_factory=list)


class OutlineChapterOut(BaseModel):
    id: str
    title: str
    summary: str | None
    key_points: list[str]
    target_pages: int


class OutlineChapterDTO(BaseModel):
    """⭐ M1 contract:GET /outline 返的 chapter 嵌入对象。

    旧 schema 是 ``dict[str, Any]``,前端只能字符串字面量切字段;现在收紧
    成 Pydantic model,FastAPI 自动 OpenAPI 暴露字段名 + 类型。
    """

    id: str
    title: str
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    target_pages: int
    index: int
    status: ChapterStatusEnum
    chapter_model: str | None = None
    # ⭐ R-15 配套:R-14 partial / 完整正文从 outline 端点暴露给前端 hydrate
    final_text: str | None = None


class OutlineResponse(BaseModel):
    project_id: int
    run_id: int | None
    status: ProjectStatus
    chapters: list[OutlineChapterDTO]


class DocumentUploadResponse(BaseModel):
    id: int
    project_id: int
    kind: Literal["tech_spec", "scoring", "template"]
    original_filename: str
    file_size: int
    extract_error: str | None = None
