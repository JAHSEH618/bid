"""initial schema + default admin

Revision ID: 0001
Revises:
Create Date: 2026-05-01

§9 完整移植。**手动维护**(不走 autogenerate),覆盖:
- 10 张表 + 索引(含 D-AR/D-BF processing_started_at + ix_chapters_processing
  D-BZ partial 索引)
- token_usage.project_id ondelete=CASCADE(FR-1.6)
- DocxJob:arq_job_id partial unique(D-S)+ project_inflight partial unique
  (D-BQ:finalizing 也算 in-flight)
- review_events.aborted=false(D-BI)
- 默认 admin 用户(passlib bcrypt rounds=12,must_change_password=true)
"""
from __future__ import annotations

import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from passlib.hash import bcrypt

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === users ===
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "must_change_password",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # === api_keys ===
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider", sa.String(32), nullable=False, server_default="dashscope"
        ),
        sa.Column("encrypted_key", sa.LargeBinary, nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "provider"),
    )

    # === projects ===
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("status", sa.String(32), nullable=False, server_default="init"),
        sa.Column(
            "created_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "api_key_owner",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        # ⭐ D-C 真快照
        sa.Column("encrypted_api_key_snapshot", sa.LargeBinary),
        sa.Column("dir_path", sa.String(512), nullable=False),
        sa.Column(
            "pages_per_chapter",
            sa.Integer,
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "max_retry_per_chapter",
            sa.Integer,
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    # === documents ===
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("markdown_path", sa.String(512)),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("extract_error", sa.String(2000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # === runs ===
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "langgraph_thread_id",
            sa.String(64),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("error", sa.String(4000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # === chapters ===
    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer,
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.String(1000)),
        sa.Column("key_points", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("target_pages", sa.Integer, nullable=False, server_default="3"),
        sa.Column("final_text", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(4000)),
        # ⭐ D-AR / D-BF
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "index"),
    )
    # ⭐ D-BZ:精准 partial WHERE,只让"真在中间态"的章节进索引
    op.create_index(
        "ix_chapters_processing",
        "chapters",
        ["status", "processing_started_at"],
        postgresql_where=sa.text(
            "status IN ('reviewing','retrying','generating') "
            "OR (status='pending' AND processing_started_at IS NOT NULL)"
        ),
    )

    # === chapter_versions ===
    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "chapter_id",
            sa.Integer,
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body_markdown", sa.Text, nullable=False),
        sa.Column("feedback_in", sa.String(4000)),
        sa.Column("decision", sa.String(16)),
        sa.Column(
            "abandoned",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("chapter_id", "version"),
    )

    # === review_events ===
    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "chapter_id",
            sa.Integer,
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("feedback_text", sa.String(4000)),
        # ⭐ D-BI:SlotLost 撤销 approve/skip 时标 true
        sa.Column(
            "aborted",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # === token_usage ===
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ⭐ FR-1.6:删除项目连带删 TokenUsage
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "run_id",
            sa.Integer,
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_token_usage_user_month",
        "token_usage",
        ["user_id", "created_at"],
    )
    op.create_index("ix_token_usage_project", "token_usage", ["project_id"])

    # === docx_jobs ===
    op.create_table(
        "docx_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ⭐ D-S:nullable=True;入队前先 INSERT 占位拿主键 id,enqueue 后再 UPDATE arq_job_id
        sa.Column("arq_job_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(4000)),
        sa.Column("output_path", sa.String(512)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # ⭐ D-BH:每次 status 切换 task 显式 SET;cleanup 基于这个判超时
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_docx_jobs_arq_job_id",
        "docx_jobs",
        ["arq_job_id"],
        unique=True,
        postgresql_where=sa.text("arq_job_id IS NOT NULL"),
    )
    # ⭐ D-BQ:finalizing 也是 in-flight,partial unique 必须覆盖
    op.create_index(
        "uq_docx_jobs_project_inflight",
        "docx_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending','rendering_mermaid','pandoc','finalizing')"
        ),
    )

    # === 默认 admin ===
    pwd = os.environ.get("ADMIN_DEFAULT_PASSWORD", "admin123")
    pwd_hash = bcrypt.using(rounds=12).hash(pwd)
    username = os.environ.get("ADMIN_DEFAULT_USERNAME", "admin")
    op.execute(
        sa.text(
            "INSERT INTO users (username, password_hash, role, is_active, "
            "must_change_password) VALUES (:u, :p, 'admin', true, true)"
        ).bindparams(u=username, p=pwd_hash)
    )


def downgrade() -> None:
    # ⚠️ partial unique 索引随表 drop,不需要单独 op.drop_index
    for t in [
        "docx_jobs",
        "token_usage",
        "review_events",
        "chapter_versions",
        "chapters",
        "runs",
        "documents",
        "projects",
        "api_keys",
        "users",
    ]:
        op.drop_table(t)
