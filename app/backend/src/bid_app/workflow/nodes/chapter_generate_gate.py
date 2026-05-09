"""章节正文生成前的人工确认节点。

每章进入 LLM-2 正文生成前先 interrupt,给前端一个选择/确认本章模型
的时机。用户在审核页点击"生成本章"后,由 /chapters/{idx}/generate
恢复工作流,再进入 write_chapter。
"""
from __future__ import annotations

from typing import Any

import structlog
from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    idx = state["current_index"]
    chapters = state.get("chapters") or []
    chapter = chapters[idx] if idx < len(chapters) else {}

    await sync_project_status(pid, "awaiting_review")
    await publish_event(
        pid,
        "chapter_ready_to_generate",
        chapter_index=idx,
        chapter_title=chapter.get("title"),
    )

    payload = interrupt(
        {
            "kind": "chapter_generate",
            "chapter_index": idx,
            "chapter_title": chapter.get("title"),
        }
    )
    if (payload or {}).get("kind") != "chapter_generate":
        log.warning(
            "chapter_generate_gate_unexpected_payload",
            project_id=pid,
            chapter_index=idx,
            payload=payload,
        )

    await sync_project_status(pid, "running")
    return {"_prefetch_chapters": bool((payload or {}).get("parallel"))}
