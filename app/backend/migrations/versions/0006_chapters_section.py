"""chapters.section for hierarchical TOC (PR-M8-2 follow-up)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-14

PR-M8-2 follow-up:LLM-1 改为输出层级目录(``章 → 节``);parse_outline 把
树展平到 chapters[],每个叶子带 ``section`` 编号(``"1.1"`` / ``"2.3.1"``)。
本迁移给 chapters 表加 ``section TEXT NULL`` 列,让 (run_id, index, section)
在 GET /outline / PUT /outline 两个方向都 round-trip。

老项目 chapters.section 是 NULL;前端 fallback 按 index+1 显示扁平编号。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("section", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chapters", "section")
