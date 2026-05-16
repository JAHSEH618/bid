"""project.blackboard_entities for structured entity buckets

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-16

Phase 1A:把材料黑板从单文件 HTML 升级为 10 个固定实体桶的结构化 JSON,
作为 LLM-1 outline / LLM-2 chapter 的检索源(后续 Phase 2 接 tool calling)。

新字段:
  - ``projects.blackboard_entities JSONB NULL``
    形状 ``{bucket_name: [{tags, content, source_doc?, section?}, ...]}``
    10 个固定 bucket: project_info / company_info / personnel_info /
    scoring_rules / technical_requirements / qualification_requirements /
    timeline_constraints / commercial_terms / compliance_constraints /
    risk_signals。NULL = ``categorize_blackboard`` 节点尚未跑(老项目 / 失败)。

WorkflowState.schema_version 同步 bump 到 3 —— 老 v2 checkpoint 没有
``blackboard_entities`` 字段,resume 时会被 ``ensure_v3_state`` 拒掉,
worker 把项目标 ``aborted_schema_v1``。运维上线前必须跑
``flush_running_workflows`` CLI 清退在跑项目(D1 断旧续新)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "blackboard_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "blackboard_entities")
