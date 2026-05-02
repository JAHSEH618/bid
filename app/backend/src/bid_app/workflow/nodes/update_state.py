"""状态机更新节点(§10.4 / v10 §4.5.7)。

读 ``_review_decision``(由 ``human_review`` interrupt 注入),决定
``current_index`` / ``retry_count`` / ``finalized_chapters`` /
``revision_feedback`` 怎么变。

三决策语义:
  - approve:章节通过 → finalized + index+1 + retry_count=0
  - skip:章节人工跳过 → finalized 占位 marker + index+1 + retry_count=0
  - revise:重写;current_index 不变,retry_count+1,把反馈塞回 LLM-2;
            超 ``max_retry_per_chapter`` 时强制累积 + 跳过(D-AC)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db

log = structlog.get_logger()


def _real_run(run_id: int | None) -> bool:
    return run_id is not None and run_id > 0


async def _safe_sync(run_id: int | None, index: int, **fields: Any) -> None:
    if not _real_run(run_id):
        return
    try:
        await sync_chapter_to_db(run_id, index, **fields)  # type: ignore[arg-type]
    except Exception:
        log.exception(
            "update_state_sync_failed", run_id=run_id, index=index, fields=fields
        )


async def run(state: WorkflowState) -> dict[str, Any]:
    decision = state.get("_review_decision", "approve")
    feedback = state.get("_review_feedback", "")
    current = state["current_index"]
    chapter = state["chapters"][current]
    pending_md: str = state.get("_pending_chapter_text", "")

    finalized = list(state.get("finalized_chapters") or [])
    run_id = state.get("run_id")
    pid = state["project_id"]

    if decision == "approve":
        finalized.append(pending_md)
        await _safe_sync(
            run_id, current, status="approved", final_text=pending_md
        )
        await publish_event(pid, "chapter_approved", chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    if decision == "skip":
        skip_marker = f"<!-- ⚠️ 章节《{chapter['title']}》被人工跳过 -->\n"
        finalized.append(skip_marker)
        await _safe_sync(
            run_id, current, status="skipped", final_text=skip_marker
        )
        await publish_event(pid, "chapter_skipped", chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    # decision == "revise"
    new_retry = state.get("retry_count", 0) + 1
    max_retry = state.get("max_retry_per_chapter", 3)
    # ⭐ 语义:max_retry_per_chapter=N 表示**允许 N 次重写**;
    # 第 N+1 次"不通过"才强制 skip。所以判定用 > 而不是 >=。
    if new_retry > max_retry:
        skip_marker = (
            f"<!-- ⚠️ 章节《{chapter['title']}》重写超限({max_retry} 次)"
            f"被强制累积 -->\n{pending_md}"
        )
        finalized.append(skip_marker)
        await _safe_sync(
            run_id, current, status="skipped", final_text=skip_marker
        )
        await publish_event(pid, "chapter_max_retry_skip", chapter_index=current)
        return {
            "current_index": current + 1,
            "retry_count": 0,
            "finalized_chapters": finalized,
            "revision_feedback": "",
        }

    # 正常重写:current_index 不变,retry_count+1,带反馈
    # ⭐ D-BK:每个切 generating 的位置都同步写 processing_started_at
    await _safe_sync(
        run_id,
        current,
        status="generating",
        retry_count=new_retry,
        processing_started_at=datetime.now(timezone.utc),
    )
    return {
        "current_index": current,
        "retry_count": new_retry,
        "revision_feedback": feedback,
    }
