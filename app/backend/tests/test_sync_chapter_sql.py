"""D-EL regression: ``references`` 是 PostgreSQL 保留关键字,
SET 子句必须用双引号包裹列名,否则 PG 把它当 FK 子句报 syntax error。
"""
from __future__ import annotations

from bid_app.workflow.sync import _build_update_sql


def test_references_column_quoted_in_set_clause() -> None:
    """``references`` 列在 SET 子句里必须用双引号。"""
    sql = _build_update_sql({"references": []})
    assert '"references"' in sql
    assert "CAST(:references AS JSONB)" in sql


def test_other_columns_also_quoted() -> None:
    """普通列名也加双引号,统一风格,避免后续再有保留关键字翻车。"""
    sql = _build_update_sql({"status": "approved"})
    assert '"status"=:status' in sql


def test_multiple_fields_keep_jsonb_cast() -> None:
    """status + references 一起 update,各自的 SET 片段都正确。"""
    sql = _build_update_sql({"status": "approved", "references": []})
    assert '"status"=:status' in sql
    assert '"references"=CAST(:references AS JSONB)' in sql
    # WHERE 条件不变
    assert "WHERE run_id=:r AND index=:i" in sql


def test_disallowed_field_rejected() -> None:
    """白名单仍然生效,防 SQL 注入。"""
    import pytest

    with pytest.raises(ValueError, match="disallowed"):
        _build_update_sql({"DROP TABLE users; --": "x"})
