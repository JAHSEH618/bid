"""chapter.references for D-EL reference panel

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-19

D-EL (Phase B):为 Chapter 加 ``references`` 列。write_chapter 节点把
LLM-2 生成本章正文时看过的实体黑板条目(首轮 BM25/混合召回结果 + tool 调用
结果,去重后)落库。前端 ChapterReviewPage 展示「本章参考的资料」列表,
让用户知道这一章的依据来自哪些上传材料。

新字段:
  - ``chapters.references JSONB NULL``
    每条:``{bucket, content, retrieval_method, score, source_doc?, section?}``
    NULL = 老 chapter / 未跑过混合召回。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("references", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chapters", "references")
