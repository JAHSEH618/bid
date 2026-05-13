"""PR-M9-1 tests:pick_chapter 选择性生成 + route_after_pick。

只跑节点逻辑,不接 DB / SSE — sync helpers monkeypatch 成 no-op。
"""

from __future__ import annotations

import pytest

from bid_app.workflow.graph import _route_after_pick
from bid_app.workflow.nodes import pick_chapter


@pytest.fixture(autouse=True)
def _stub_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_publish(*_args: object, **_kw: object) -> None:
        return None

    async def _noop_sync(*_args: object, **_kw: object) -> None:
        return None

    monkeypatch.setattr(pick_chapter, "publish_event", _noop_publish)
    monkeypatch.setattr(pick_chapter, "sync_chapter_to_db", _noop_sync)


@pytest.mark.asyncio
async def test_no_selection_keeps_current_index() -> None:
    """selected_chapter_ids 缺失 → 全选,不动 current_index。"""
    state = {
        "project_id": 1,
        "current_index": 0,
        "chapters": [{"id": "ch_01"}, {"id": "ch_02"}],
    }
    out = await pick_chapter.run(state)
    assert out == {}


@pytest.mark.asyncio
async def test_empty_selection_keeps_current_index() -> None:
    """selected_chapter_ids=[] 与 None 等价(全选)。"""
    state = {
        "project_id": 1,
        "current_index": 0,
        "chapters": [{"id": "ch_01"}],
        "selected_chapter_ids": [],
    }
    out = await pick_chapter.run(state)
    assert out == {}


@pytest.mark.asyncio
async def test_unselected_chapter_advances_index() -> None:
    """ch_01 / ch_02 / ch_03,选 ch_01 + ch_03 → 跳过 ch_02,落到 ch_03。"""
    state = {
        "project_id": 1,
        "current_index": 1,  # 当前指向 ch_02
        "chapters": [
            {"id": "ch_01"},
            {"id": "ch_02"},
            {"id": "ch_03"},
        ],
        "selected_chapter_ids": ["ch_01", "ch_03"],
    }
    out = await pick_chapter.run(state)
    assert out["current_index"] == 2
    assert out["retry_count"] == 0


@pytest.mark.asyncio
async def test_skip_multiple_unselected_in_a_row() -> None:
    """ch_02 / ch_03 / ch_04 都不在 selected,从 ch_02 一口气跳到 ch_05。"""
    state = {
        "project_id": 1,
        "current_index": 1,
        "chapters": [
            {"id": "ch_01"},
            {"id": "ch_02"},
            {"id": "ch_03"},
            {"id": "ch_04"},
            {"id": "ch_05"},
        ],
        "selected_chapter_ids": ["ch_01", "ch_05"],
    }
    out = await pick_chapter.run(state)
    assert out["current_index"] == 4


@pytest.mark.asyncio
async def test_exhausted_returns_index_past_end() -> None:
    """ch_03/ch_04 都不在 selected,游标超过 chapters 长度 → 标记结束。"""
    state = {
        "project_id": 1,
        "current_index": 2,
        "chapters": [
            {"id": "ch_01"},
            {"id": "ch_02"},
            {"id": "ch_03"},
            {"id": "ch_04"},
        ],
        "selected_chapter_ids": ["ch_01"],
    }
    out = await pick_chapter.run(state)
    assert out["current_index"] == 4
    assert out["retry_count"] == 0


def test_route_after_pick_assemble_when_exhausted() -> None:
    state = {"current_index": 5, "chapters": [{}, {}, {}, {}, {}]}
    assert _route_after_pick(state) == "assemble"


def test_route_after_pick_generate_when_more_chapters() -> None:
    state = {"current_index": 1, "chapters": [{}, {}, {}]}
    assert _route_after_pick(state) == "chapter_generate_gate"


def test_route_after_pick_default_when_no_chapters() -> None:
    """空 chapters + 0 index → 立刻 assemble (走完)。"""
    state: dict[str, object] = {"current_index": 0, "chapters": []}
    assert _route_after_pick(state) == "assemble"
