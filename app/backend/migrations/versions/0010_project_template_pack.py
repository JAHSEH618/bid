"""project.template_pack for D-EF template skeleton pack id

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-18

D-EF (Stage 1):为 Project 加 ``template_pack`` 字段,记录本项目用的模版骨架包
id(如 ``gov_consumer_platform_v1``)。generate_outline 节点根据
material_understanding.project_category 选骨架并写回这里,后续 revise /
resume 沿用同一份骨架,workflow 校验器据此决定模版规则集。

WorkflowState.schema_version 同步 bump 3 → 4(state.py:17)。老 v3
checkpoint 没有 ``template_pack`` 字段,resume 时会被 ``ensure_current_state``
拒掉,worker 把项目标 ``aborted_schema_v1``。运维上线前必须跑
``flush_running_workflows`` CLI 清退在跑项目(D1 断旧续新)。

新字段:
  - ``projects.template_pack VARCHAR(64) NULL``
    NULL = 项目在 stage 1 之前启动 / 骨架特性被关闭。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("template_pack", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "template_pack")
