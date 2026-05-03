"""P5 章节审核 interrupt 节点(§10.6b / v10 §4.5.5)。

触发前章节正文已由 ``merge_chapter`` 节点放入 ``state['_pending_chapter_text']``,
DB Chapter 状态由 ``write_chapter`` 节点写成 ``generating``。

本节点(R-7 修复后)做三件事:
  1. 把 DB Chapter 切 ``awaiting_review`` + ``processing_started_at=None``
     (修复 R-7:之前漏了这一步,前端永远看到 chapter 卡 generating)
  2. 把 Project 切 ``awaiting_review`` + SSE 通知
  3. ``interrupt(...)`` 暂停;后续由 /review → resume_review_task 注入

resume payload 形状::

    {"decision": "approve" | "revise" | "skip",
     "feedback": "..."}    # 选 revise 时必填

下游 ``update_state`` 节点接 ``_review_decision`` / ``_review_feedback``。
"""
from __future__ import annotations

from typing import Any

import structlog
from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db, sync_project_status

log = structlog.get_logger()


def _real_run(run_id: int | None) -> bool:
    """与 write_chapter / update_state 同口径:run_id > 0 才视为真 DB 路径
    (CLI run_local 走 -1)。
    """
    return run_id is not None and run_id > 0


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    run_id = state.get("run_id")
    idx = state["current_index"]
    full_chapter = state.get("_pending_chapter_text", "")

    # ⭐ R-7 修复:把 chapter 从 generating → awaiting_review,清
    # processing_started_at(D-AR / D-BF cleanup 不再误回滚)
    if _real_run(run_id):
        try:
            await sync_chapter_to_db(
                run_id,  # type: ignore[arg-type]
                idx,
                status="awaiting_review",
                processing_started_at=None,
            )
        except Exception:
            log.exception(
                "human_review_chapter_sync_failed",
                run_id=run_id,
                index=idx,
            )

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
