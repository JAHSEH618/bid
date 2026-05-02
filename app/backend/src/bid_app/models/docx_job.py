"""DocxJob 表(§8)。

⭐ status 取值:``pending | rendering_mermaid | pandoc | finalizing |
done | failed | invalidated``。

⭐ D-BQ:``finalizing`` = "tmp 文件已生成,正在 atomic rename 成
``proposal.docx``"。``done`` 的语义收紧为"rename 已成功 + 文件可下载"。

⭐ D-CG:``invalidated`` = "上游 markdown 重新生成,本 DOCX 产物已过期";
由 assemble 节点同步标记。

partial unique index 在 migration 里建(§9):
- ``uq_docx_jobs_arq_job_id``  : ``(arq_job_id) WHERE arq_job_id IS NOT NULL``
- ``uq_docx_jobs_project_inflight`` : ``(project_id) WHERE status IN
  ('pending','rendering_mermaid','pandoc','finalizing')``
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class DocxJob(Base, TimestampMixin):
    __tablename__ = "docx_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    arq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ⭐ D-BH:每次 status 切换 SET updated_at=NOW();cron
    # cleanup_stale_docx_jobs 用 updated_at 而不是 created_at 判超时,
    # 避免误杀"等串行锁/真在跑 pandoc"的 job
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_func.now(),
        onupdate=sa_func.now(),
        nullable=False,
    )
