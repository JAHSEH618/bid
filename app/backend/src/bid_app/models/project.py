"""Project 表(§8)。

⚠️ status 取值:``init | extracting | outlining | outline_ready | queued |
running | awaiting_review | done | failed | aborted``。
``queued`` 表示项目已 ``/start`` 但全局并发上限已满,排队等位(D-P)。

⭐ D-C 真快照:``encrypted_api_key_snapshot`` 在 ``/start`` 时由 ApiKey 拷过来,
工作流后续都从这里读;用户重置 / 删除 ApiKey 不影响本项目(FR-7.6)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="init")
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    api_key_owner: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    encrypted_api_key_snapshot: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    dir_path: Mapped[str] = mapped_column(String(512))
    pages_per_chapter: Mapped[int] = mapped_column(default=3)
    max_retry_per_chapter: Mapped[int] = mapped_column(default=3)
