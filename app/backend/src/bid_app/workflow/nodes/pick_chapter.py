"""选取当前章节(对应 v10 §4.5.1)。

state.current_index 就是循环游标;此节点只负责:
- 通知前端 ``chapter_started_pick``(让 P5 高亮当前章节)
- 不改任何核心 Loop 变量,仅做 SSE / log

返回空 dict(LangGraph 不更新 state)。
"""
from __future__ import annotations

from typing import Any

import structlog

from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = state["project_id"]
    idx = state["current_index"]
    chapters = state.get("chapters") or []

    if idx >= len(chapters):
        log.warning(
            "pick_chapter_out_of_range",
            project_id=project_id,
            idx=idx,
            total=len(chapters),
        )
        return {}

    current = chapters[idx]
    await publish_event(
        project_id,
        "chapter_picked",
        chapter_index=idx,
        chapter_title=current.get("title"),
        retry_count=state.get("retry_count", 0),
    )
    return {}
