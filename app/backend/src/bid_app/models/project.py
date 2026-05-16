"""Project 表(§8 + PR-M7-1 v2 schema bump)。

⚠️ status 取值:``init | extracting | outlining | outline_ready | queued |
running | awaiting_review | done | failed | aborted | aborted_schema_v1``。
``queued`` 表示项目已 ``/start`` 但全局并发上限已满,排队等位(D-P)。
``aborted_schema_v1`` (PR-M7-1) = v1 checkpoint 在 v2 graph 上无法 resume。

⭐ D-C 真快照:``encrypted_api_key_snapshot`` 在 ``/start`` 时由 ApiKey 拷过来,
工作流后续都从这里读;用户重置 / 删除 ApiKey 不影响本项目(FR-7.6)。

⭐ PR-M7-3:``blackboard_path`` 指向 ``/var/lib/bid-app/projects/{id}/blackboard.html``,
disk + DB 双写;备份脚本同步覆盖该目录。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="init")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    api_key_owner: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    encrypted_api_key_snapshot: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dir_path: Mapped[str] = mapped_column(String(512))
    pages_per_chapter: Mapped[int] = mapped_column(default=3)
    max_retry_per_chapter: Mapped[int] = mapped_column(default=3)

    # ⭐ 模型快照(§0002):/start 时从 User 拷贝,与 D-C ApiKey 快照模式一致
    # NULL 时工作流回退到 settings 默认模型
    outline_model_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chapter_model_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    visuals_model_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ⭐ PR-M7-3:HTML 黑板的磁盘路径
    blackboard_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ⭐ Phase 1A (2026-05-16):结构化实体桶 JSON,categorize_blackboard
    # 节点写入,LLM-1 / LLM-2 从这里读结构化上下文(取代直接吃 markdown 截断)。
    # 形状 {bucket_name: [{tags, content, source_doc?, section?}, ...]}。
    # 用 PG JSONB(迁移 0008 也是 JSONB)对齐 schema 类型,避免
    # Alembic autogenerate 把通用 JSON ↔ JSONB 看成 diff 制造噪音。
    blackboard_entities: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
