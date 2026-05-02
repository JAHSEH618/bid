"""ReviewEvent 表(§8)。

⭐ D-BI:SlotLost 把 approve/skip 章节回滚到 awaiting_review 时,本事件
**没真正生效**,标 ``aborted=true`` 避免前端"上次审核人/决策"误显示已撤销动作。
默认 false,正常审核流程不动这个字段;查询"最近一次有效审核"加
``WHERE NOT aborted``。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ReviewEvent(Base, TimestampMixin):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE")
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(16))
    # approve|revise|skip|retry_failed
    feedback_text: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    aborted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
