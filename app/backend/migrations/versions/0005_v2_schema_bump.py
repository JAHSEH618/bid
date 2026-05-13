"""v2 schema bump (PR-M7-1)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13

⭐ 一次性把 M7-M9 用到的字段全部迁完,后续 PR 不再做 schema 改动:

- documents
    · ``kind`` → nullable (D5 + PR-M7-2:取消三选一约束)
    · ``tags TEXT[]`` (用户给文档打标签,代替 kind 主导分类)
    · ``structured_html TEXT`` (PR-M7-3:抽取后的清洗 HTML 落黑板用)
    · ``byte_size BIGINT`` (D5:项目级总和限额校验用)
    · ``mime_type TEXT``
- projects
    · ``blackboard_path TEXT`` (PR-M7-3:磁盘黑板路径,DB-disk 双写口径)

WorkflowState 字段升级在 ``workflow/state.py`` 完成 (langgraph
checkpoint 落 JSONB,无需 alembic);本迁移**不**变更 ``langgraph_*`` 表。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # documents
    op.alter_column(
        "documents",
        "kind",
        existing_type=sa.String(16),
        nullable=True,
    )
    op.add_column(
        "documents",
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("structured_html", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("mime_type", sa.Text(), nullable=True),
    )
    # 历史行回填:byte_size 用 file_size,避免下游 SUM(byte_size) 漏算老数据
    op.execute(
        sa.text(
            "UPDATE documents SET byte_size = file_size "
            "WHERE byte_size IS NULL AND file_size IS NOT NULL"
        )
    )

    # projects
    op.add_column(
        "projects",
        sa.Column("blackboard_path", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "blackboard_path")
    op.drop_column("documents", "mime_type")
    op.drop_column("documents", "byte_size")
    op.drop_column("documents", "structured_html")
    op.drop_column("documents", "tags")
    op.alter_column(
        "documents",
        "kind",
        existing_type=sa.String(16),
        nullable=False,
    )
