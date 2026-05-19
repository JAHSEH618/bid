"""project.blackboard_embeddings for D-EK hybrid retrieval

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-19

D-EK (Phase A):为 Project 加 ``blackboard_embeddings`` 列。categorize_blackboard
节点把全部桶 entries 一次性 embed(DashScope text-embedding-v3, 1024 维),
落到这个 JSONB 列;workflow resume 时 BlackboardIndex 复用,避免重算。

形状与 ``blackboard_entities`` 严格对齐:
  ``{bucket_name: [vec_for_entry_0_as_list_of_float, ...]}``

NULL = 节点尚未跑 / embedding 服务关闭 / 失败回退。下游 BlackboardIndex
检测到 None 自动退化纯 BM25,工作流不阻塞。

WorkflowState.schema_version 同步 bump 4 → 5(state.py:17)。

新字段:
  - ``projects.blackboard_embeddings JSONB NULL``
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("blackboard_embeddings", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "blackboard_embeddings")
