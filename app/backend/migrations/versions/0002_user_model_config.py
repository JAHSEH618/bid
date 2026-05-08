"""add user model config + project model snapshot

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-14

用户可自定义三类任务使用的百炼模型:
  - users 表加 llm1_outline_model / llm2_chapter_model / llm3_visuals_model
  - projects 表加快照字段(与 D-C ApiKey 快照模式一致):/start 时拷贝,
    后续工作流从这里读;用户改模型不影响已在跑的项目
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === users: 三类模型配置(均为 nullable, NULL 时走 settings 默认值) ===
    op.add_column(
        "users",
        sa.Column("llm1_outline_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("llm2_chapter_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("llm3_visuals_model", sa.String(128), nullable=True),
    )

    # === projects: 模型快照(/start 时从 User 拷贝,与 D-C ApiKey 快照模式一致) ===
    op.add_column(
        "projects",
        sa.Column("outline_model_snapshot", sa.String(128), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("chapter_model_snapshot", sa.String(128), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("visuals_model_snapshot", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "visuals_model_snapshot")
    op.drop_column("projects", "chapter_model_snapshot")
    op.drop_column("projects", "outline_model_snapshot")
    op.drop_column("users", "llm3_visuals_model")
    op.drop_column("users", "llm2_chapter_model")
    op.drop_column("users", "llm1_outline_model")
