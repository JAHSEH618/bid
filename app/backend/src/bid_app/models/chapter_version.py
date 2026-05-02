"""ChapterVersion 表(§8)。

⭐ FR-4.7:章节 retry_failed 时,本轮所有未审版本标 ``abandoned=true``,
保留历史不删除,但全文整合 / 列表查询默认过滤掉。
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ChapterVersion(Base, TimestampMixin):
    __tablename__ = "chapter_versions"
    __table_args__ = (UniqueConstraint("chapter_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    body_markdown: Mapped[str] = mapped_column(Text)
    feedback_in: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # approve|revise|skip|retry_failed|None(未审)
    abandoned: Mapped[bool] = mapped_column(Boolean, default=False)
