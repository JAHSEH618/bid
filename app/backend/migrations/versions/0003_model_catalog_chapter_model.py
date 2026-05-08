"""add model catalog and chapter model snapshot

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-08

设置页只维护用户可选模型池;具体工作流启动/章节生成时再选择模型。
章节正文模型下沉到 chapters.model_snapshot,支持每章不同 LLM-2。
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("model_catalog", sa.JSON(), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("model_snapshot", sa.String(128), nullable=True),
    )
    op.alter_column(
        "token_usage",
        "model",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "token_usage",
        "model",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.drop_column("chapters", "model_snapshot")
    op.drop_column("users", "model_catalog")
