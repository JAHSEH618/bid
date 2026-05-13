"""PR-M7-1: WorkflowState v1 → v2 schema gate 单元测试。

DB-level alembic round-trip 测试需要真实 PG,放在 RUNTIME_TEST_REPORT 的
集成步骤里手动跑。本文件只验证 ``ensure_v2_state`` 的 happy / sad path。
"""

from __future__ import annotations

import pytest

from bid_app.workflow.state import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    WorkflowSchemaMismatch,
    ensure_v2_state,
)


def test_v2_state_passes() -> None:
    state = {
        "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
        "project_id": 1,
        "run_id": 2,
    }
    # 不抛 = pass
    ensure_v2_state(state)


def test_v1_state_missing_schema_version_raises() -> None:
    state = {"project_id": 1, "run_id": 2}
    with pytest.raises(WorkflowSchemaMismatch) as exc:
        ensure_v2_state(state)
    assert exc.value.found is None
    assert exc.value.current == CURRENT_WORKFLOW_SCHEMA_VERSION


def test_state_with_other_schema_version_raises() -> None:
    state = {"schema_version": 99, "project_id": 1}
    with pytest.raises(WorkflowSchemaMismatch) as exc:
        ensure_v2_state(state)
    assert exc.value.found == 99


def test_exception_message_includes_versions() -> None:
    state = {"project_id": 1}
    with pytest.raises(WorkflowSchemaMismatch) as exc:
        ensure_v2_state(state)
    msg = str(exc.value)
    assert str(CURRENT_WORKFLOW_SCHEMA_VERSION) in msg
    assert "rebuild the project" in msg
