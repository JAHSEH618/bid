"""Pydantic v2 IO schemas — projects 相关。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ⭐ M1 contract:Project.status 全集(单一来源,API 收紧用)
# init / queued:刚建项目还没 /start
# extracting / outlining:/start 后 LangGraph 阶段
# awaiting_material_understanding (PR-M8-1):material_understanding_review 节点
#   interrupt,等用户在 MaterialUnderstandingPage 上点 pass / revise / skip
# outline_ready:LLM-1 跑完,等用户在 P4 编辑提纲并 /confirm-outline
# running / awaiting_review:章节循环阶段(P5)
# done / failed / aborted:终态
# aborted_v1 (PR-M7-1):v2 上线时 ``flush_running_workflows`` CLI 把所有
#   in-flight v1 项目标记为该状态,提示用户重建。
# aborted_schema_v1 (PR-M7-1):worker 运行时检测到 ``WorkflowSchemaMismatch``
#   (老 checkpoint + 新 graph)时自动标记。
ProjectStatus = Literal[
    "init",
    "queued",
    "extracting",
    "awaiting_material_understanding",
    "outlining",
    "outline_ready",
    "running",
    "awaiting_review",
    "done",
    "failed",
    "aborted",
    "aborted_v1",
    "aborted_schema_v1",
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
    # JOIN users.username 后填入;User 被删的极端情况留 None。
    created_by_username: str | None = None
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
    # PR-M8-2 follow-up:层级编号 "1.1" / "2.3.1";None 时后端按 index+1 兜底
    section: str | None = None
    title: str = Field(..., min_length=1)
    summary: str | None = None
    key_points: list[str] = Field(..., min_length=1)
    target_pages: int = Field(..., ge=1, le=10)
    matched_scoring_items: list[str] = Field(default_factory=list)
    chapter_model: str | None = Field(None, max_length=128)


class OutlineConfirmRequest(BaseModel):
    """body.chapters 为空数组 / 缺失 → 自动确认沿用 LLM-1。

    PR-M9-1:可选 ``selected_chapter_ids`` 让前端在锁定目录时一并提交
    用户勾选的章节;空 / None → 全选(向后兼容)。

    textarea TOC + revise(PR-M8-2 follow-up #2):
    - ``decision="confirm"``(默认):提交 ``chapters`` 锁定目录
    - ``decision="revise"``:把 ``feedback`` 送 LLM-1 重新生成大纲
    """

    decision: Literal["confirm", "revise"] = "confirm"
    feedback: str | None = None
    chapters: list[OutlineChapterIn] = Field(default_factory=list)
    selected_chapter_ids: list[str] | None = Field(default=None)


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
    # PR-M8-2 follow-up:层级目录编号 "1.1" / "2.3.1";老项目可能 None
    section: str | None = None
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
    max_concurrent_chapter_generations: int
    chapters: list[OutlineChapterDTO]


class DocumentUploadResponse(BaseModel):
    id: int
    project_id: int
    # PR-M7-2:kind 不再强制三选一;tags 取代分类语义。
    kind: str | None = None
    original_filename: str
    file_size: int
    byte_size: int | None = None
    mime_type: str | None = None
    tags: list[str] | None = None
    extract_error: str | None = None
    # PR-M7-2:异步抽取状态。pending = 抽取 task 已入队但未跑完。
    extract_status: Literal["pending", "done", "failed"] = "pending"
