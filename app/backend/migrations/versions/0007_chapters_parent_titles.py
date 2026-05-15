"""chapters.parent_titles for TOC group reconstruction

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-15

textarea TOC editor 让用户能改完整目录,但 ``parse_outline._flatten_toc``
只保留叶子,把 LLM-1 输出里"第一章 项目背景" / "第二章 技术方案" 这样的
分组标题扔了。Round-trip 时 ``chaptersToTocText`` 没有原始分组标题可显示,
只能用 "章节分组" 占位 — UI 上很难看。

本迁移给 ``chapters`` 加 ``parent_titles JSONB NULL``,保存该叶子从根到
父节点的全部祖先标题(``["项目背景","招标方现状"]``)。前端
``chaptersToTocText`` 据此重建分组行;老项目 parent_titles=NULL 时按
"第 N 章" 中文数字兜底。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("parent_titles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chapters", "parent_titles")
