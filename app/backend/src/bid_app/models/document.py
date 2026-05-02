"""Document 表(§8)。

``kind`` ∈ {tech_spec, scoring, template}。
``markdown_path`` 是 markitdown 抽取后的 .md 落盘路径(便于排查)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))  # tech_spec|scoring|template
    original_filename: Mapped[str] = mapped_column(String(255))
    markdown_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int] = mapped_column()
    extract_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
