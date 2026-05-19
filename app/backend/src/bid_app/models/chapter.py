"""Chapter 表(§8)。

⚠️ status 取值::

    pending | generating | awaiting_review | reviewing | approved
    | skipped | failed | retrying

⭐ D-AI 中间态:
- ``reviewing``:API ``/review`` 行锁内切的;worker 接管后 → generating
  (revise) / approved / skipped(由 update_state 节点)。
- ``retrying``:API ``/retry`` 行锁内切的;worker 接管后 → pending
  (重置)→ generating。

⭐ D-AR + D-BF:``processing_started_at`` 在 reviewing/retrying/generating
切换时写;cron ``cleanup_stale_chapters`` 按状态分段超时回滚。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("run_id", "index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    # PR-M8-2 follow-up:层级编号 "1.1" / "2.3.1";老项目 NULL 时前端 fallback。
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    # textarea TOC editor 用:从根到父节点的祖先标题列表
    # ``["项目背景", "招标方现状"]`` — 让 chaptersToTocText 能重建分组行。
    parent_titles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    key_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_pages: Mapped[int] = mapped_column(default=3)
    model_snapshot: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    retry_count: Mapped[int] = mapped_column(default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    # D-EL (2026-05-19):LLM-2 生成本章正文时看过的实体黑板条目快照。
    # 形如 ``[{bucket, content, retrieval_method, score, source_doc?, section?}]``。
    # 由 write_chapter 节点把首轮 BM25/混合召回结果 + tool 调用结果去重落库,
    # 前端 ChapterReviewPage 展示「本章参考的资料」列表。NULL = 老项目 / 节点未跑。
    references: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )
