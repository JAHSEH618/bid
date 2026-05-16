"""documents.stored_path for original upload file cleanup

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-16

`upload_document` 之前把原始上传文件写到
``{project_dir}/uploads/{prefix}_{token}.{ext}``,但 Document 模型没有
持久化这个路径;``delete_document`` 只 unlink markdown_path,原始文件
留在磁盘上。用户「上传 → 删除 → 再上传」时 ``byte_size`` 总和回落,
但磁盘累积,可以绕过 500MB 项目目录上限。

本迁移给 ``documents`` 加 ``stored_path VARCHAR(512) NULL``,nullable
让老 row(没记录原始路径)兼容存活;``delete_document`` 在删除时尽力
清理原始文件,缺失视为 best-effort 跳过。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("stored_path", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "stored_path")
