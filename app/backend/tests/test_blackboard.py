"""PR-M7-3 tests:blackboard atomic write / read / delete。

这层只验证磁盘语义(在 ``tmp_path`` 下隔离),DB 路径用 monkeypatch
session_factory 替身(避免在测试里真的连 PG)。
真实 atomic rename + fsync 行为靠 pytest 之外的集成测试覆盖。

注:``tests/conftest.py`` 已设哑配置 env,bid_app.db 能正常 import。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """劫持 ``bid_app.db.session_factory``,让 blackboard 的延迟 import 拿到
    一个不真连 PG 的 dummy session。"""

    class _DummySession:
        async def __aenter__(self) -> _DummySession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, query: object, params: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    import bid_app.db as db_module

    monkeypatch.setattr(db_module, "session_factory", lambda: _DummySession())


@pytest.fixture(autouse=True)
def tmp_projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把所有黑板 IO 重定向到 tmp_path,确保不污染 /var/lib/bid-app。"""
    from bid_app.workflow import blackboard as bb_module

    async def _fake_dir_for(project_id: int) -> Path:
        return tmp_path / str(project_id)

    def _fake_dir(project_id: int) -> Path:
        return tmp_path / str(project_id)

    monkeypatch.setattr(bb_module, "_project_dir_for", _fake_dir_for)
    monkeypatch.setattr(bb_module, "_project_dir", _fake_dir)
    return tmp_path


@pytest.mark.asyncio
async def test_write_blackboard_atomic(tmp_projects_dir: Path) -> None:
    from bid_app.workflow.blackboard import write_blackboard

    final = await write_blackboard(42, "<p>hello</p>")
    expected = tmp_projects_dir / "42" / "blackboard.html"
    assert final == expected
    assert expected.read_text() == "<p>hello</p>"
    assert not (tmp_projects_dir / "42" / "blackboard.html.tmp").exists()


@pytest.mark.asyncio
async def test_write_blackboard_overwrites(
    tmp_projects_dir: Path,
) -> None:
    from bid_app.workflow.blackboard import write_blackboard

    await write_blackboard(1, "<p>v1</p>")
    await write_blackboard(1, "<p>v2</p>")
    final = tmp_projects_dir / "1" / "blackboard.html"
    assert final.read_text() == "<p>v2</p>"


@pytest.mark.asyncio
async def test_write_blackboard_disk_failure_raises(
    tmp_projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from bid_app.workflow.blackboard import (
        BlackboardWriteFailed,
        write_blackboard,
    )

    def _boom(*_args: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(BlackboardWriteFailed):
        await write_blackboard(99, "<p>x</p>")
    assert not (tmp_projects_dir / "99" / "blackboard.html.tmp").exists()


@pytest.mark.asyncio
async def test_read_blackboard_missing_raises(
    tmp_projects_dir: Path,
) -> None:
    from bid_app.workflow.blackboard import (
        BlackboardMissing,
        read_blackboard,
    )

    with pytest.raises(BlackboardMissing):
        await read_blackboard(1234)


@pytest.mark.asyncio
async def test_read_blackboard_returns_disk_content(
    tmp_projects_dir: Path,
) -> None:
    from bid_app.workflow.blackboard import (
        read_blackboard,
        write_blackboard,
    )

    await write_blackboard(7, "<h1>hello</h1>")
    out = await read_blackboard(7)
    assert out == "<h1>hello</h1>"


@pytest.mark.asyncio
async def test_delete_blackboard_removes_disk(
    tmp_projects_dir: Path,
) -> None:
    from bid_app.workflow.blackboard import (
        delete_blackboard,
        write_blackboard,
    )

    await write_blackboard(8, "<p>a</p>")
    final = tmp_projects_dir / "8" / "blackboard.html"
    assert final.exists()
    await delete_blackboard(8)
    assert not final.exists()


@pytest.mark.asyncio
async def test_delete_blackboard_idempotent_when_missing(
    tmp_projects_dir: Path,
) -> None:
    from bid_app.workflow.blackboard import delete_blackboard

    await delete_blackboard(9999)
