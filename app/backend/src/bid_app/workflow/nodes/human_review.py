"""P5 章节审核 interrupt 节点(§10.6b / v10 §4.5.5)。

触发前章节正文已由 ``merge_chapter`` 节点放入 ``state['_pending_chapter_text']``,
DB Chapter 状态由 ``write_chapter`` 节点写成 ``awaiting_review``(经 D-AI
``reviewing`` 中间态)。本节点只做项目级状态切换 + SSE 通知 + interrupt。

resume payload 形状::

    {"decision": "approve" | "revise" | "skip",
     "feedback": "..."}    # 选 revise 时必填

下游 ``update_state`` 节点接 ``_review_decision`` / ``_review_feedback``。
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    idx = state["current_index"]
    full_chapter = state.get("_pending_chapter_text", "")

    # 项目切 awaiting_review,SSE 通知前端拉章节
    await sync_project_status(pid, "awaiting_review")
    await publish_event(
        pid,
        "awaiting_review",
        chapter_index=idx,
        chapter_text=full_chapter,
    )

    # interrupt 暂停;后续由 /review → resume_review_task 注入
    payload = interrupt(
        {
            "kind": "chapter_review",
            "chapter_index": idx,
            "chapter_text": full_chapter,
        }
    )

    # resume 后:project 回 running;decision/feedback 写进 state 给 update_state
    await sync_project_status(pid, "running")
    return {
        "_review_decision": (payload or {}).get("decision", "approve"),
        "_review_feedback": (payload or {}).get("feedback", ""),
    }
