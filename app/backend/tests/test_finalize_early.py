"""D-EM: 提前合并(_finalize_early)测试。

只跑节点逻辑,DB / SSE 替换为 no-op。
"""
from __future__ import annotations

from typing import Any

import pytest

from bid_app.workflow.nodes import pick_chapter
from bid_app.workflow.prompts.assemble_prompt import assemble_proposal


@pytest.fixture(autouse=True)
def _stub_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_args: object, **_kw: object) -> None:
        return None

    monkeypatch.setattr(pick_chapter, "publish_event", _noop)
    monkeypatch.setattr(pick_chapter, "sync_chapter_to_db", _noop)


@pytest.mark.asyncio
async def test_finalize_early_marks_remaining_chapters() -> None:
    """_finalize_early=True 时 pick_chapter 把剩余章节都标 not_generated 占位。"""
    state: dict[str, Any] = {
        "project_id": 1,
        "current_index": 1,  # 已生成 ch_01,准备生成 ch_02
        "chapters": [
            {"id": "ch_01", "section": "1", "title": "第一章"},
            {"id": "ch_02", "section": "2", "title": "第二章"},
            {"id": "ch_03", "section": "3", "title": "第三章"},
        ],
        "finalized_chapters": ["## 1 第一章\n\n正文..."],
        "_finalize_early": True,
    }
    out = await pick_chapter.run(state)
    assert out["current_index"] == 3
    finalized = out["finalized_chapters"]
    # 原 1 章 + 2 章占位
    assert len(finalized) == 3
    assert "正文" in finalized[0]
    assert "（本章未生成）" in finalized[1]
    assert "## 2 第二章" in finalized[1]
    assert "（本章未生成）" in finalized[2]
    assert "## 3 第三章" in finalized[2]
    # _finalize_early 被清掉防止重入
    assert out["_finalize_early"] is False


@pytest.mark.asyncio
async def test_finalize_early_at_last_chapter_no_op() -> None:
    """current_index 已到末尾时即使带 finalize_early 也只是正常路由。"""
    state: dict[str, Any] = {
        "project_id": 1,
        "current_index": 2,
        "chapters": [
            {"id": "ch_01", "section": "1", "title": "T1"},
            {"id": "ch_02", "section": "2", "title": "T2"},
        ],
        "_finalize_early": True,
    }
    out = await pick_chapter.run(state)
    # finalize_early 分支只在 idx < len 时触发;否则走 exhausted 分支
    assert out["current_index"] == 2


@pytest.mark.asyncio
async def test_finalize_early_false_no_change() -> None:
    """_finalize_early 缺失 / False → 与旧行为一致。"""
    state: dict[str, Any] = {
        "project_id": 1,
        "current_index": 0,
        "chapters": [{"id": "ch_01"}],
    }
    out = await pick_chapter.run(state)
    assert "current_index" not in out or out.get("current_index") == 0


def test_assemble_placeholder_in_final_markdown() -> None:
    """pick_chapter 生成的占位文字经 assemble_proposal 拼接保留在最终 markdown 里。"""
    finalized = [
        "## 1 第一章\n\n第一章正文。",
        "## 2 第二章\n\n> **（本章未生成）** 该章节在用户提前合并时尚未生成正文。\n",
    ]
    md = assemble_proposal(finalized, total_chapters=2)
    assert "## 1 第一章" in md
    assert "## 2 第二章" in md
    assert "（本章未生成）" in md
    # 顺序保留
    assert md.index("第一章正文") < md.index("（本章未生成）")
