"""docx_jobs scope + chapter_id (PR-M6-2)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13

加 ``scope`` ('project' | 'chapter') + ``chapter_id`` 给单章 Word 导出用;
原 partial unique index 拆成两条:project 级 in-flight 至多 1 条 ×
chapter 级 in-flight 每章至多 1 条。串行锁仍由 ``services/docx_export.py``
全局 Redis lock 提供（D-CV 不动），本索引只保证「同一目标重复触发去重」。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新列 — 默认 'project' 让历史行行为不变
    op.add_column(
        "docx_jobs",
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default="project",
        ),
    )
    op.add_column(
        "docx_jobs",
        sa.Column(
            "chapter_id",
            sa.Integer,
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # 旧 partial unique 只按 project_id 锁定 in-flight,无法区分 scope。
    # 拆成两条 partial unique：
    #   1) project 级 in-flight: (project_id) WHERE scope='project' AND status IN (...)
    #   2) chapter 级 in-flight: (chapter_id) WHERE scope='chapter' AND status IN (...)
    op.drop_index("uq_docx_jobs_project_inflight", table_name="docx_jobs")
    op.create_index(
        "uq_docx_jobs_project_inflight_v2",
        "docx_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'project' AND status IN "
            "('pending','rendering_mermaid','pandoc','finalizing')"
        ),
    )
    op.create_index(
        "uq_docx_jobs_chapter_inflight",
        "docx_jobs",
        ["chapter_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'chapter' AND status IN "
            "('pending','rendering_mermaid','pandoc','finalizing')"
        ),
    )

    # CHECK：scope=chapter 必须有 chapter_id;scope=project 必须无 chapter_id。
    # 防止 API 误传破坏不变量。
    op.create_check_constraint(
        "ck_docx_jobs_scope_chapter_id",
        "docx_jobs",
        "(scope = 'project' AND chapter_id IS NULL) OR "
        "(scope = 'chapter' AND chapter_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_docx_jobs_scope_chapter_id", "docx_jobs", type_="check")
    op.drop_index("uq_docx_jobs_chapter_inflight", table_name="docx_jobs")
    op.drop_index("uq_docx_jobs_project_inflight_v2", table_name="docx_jobs")
    op.create_index(
        "uq_docx_jobs_project_inflight",
        "docx_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending','rendering_mermaid','pandoc','finalizing')"
        ),
    )
    op.drop_column("docx_jobs", "chapter_id")
    op.drop_column("docx_jobs", "scope")
