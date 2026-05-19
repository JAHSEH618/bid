"""user.llm4_embedding_model + project.embedding_model_snapshot for D-EO

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-19

D-EO:与 LLM-1/2/3 同级,让用户可选 embedding 模型(混合召回 query 向量化)。
- ``users.llm4_embedding_model``:用户偏好,NULL → settings.embedding_model 兜底
- ``projects.embedding_model_snapshot``:/start 时拷贝,确保运行中项目不抖

跟现有 outline / chapter / visuals 三列完全一致的 String(128) nullable。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("llm4_embedding_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("embedding_model_snapshot", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "embedding_model_snapshot")
    op.drop_column("users", "llm4_embedding_model")
