"""P4 提纲确认节点(§10.6 / D-K)。

parse_outline 已经把 LLM-1 输出落到 state.chapters。
此节点:
1. 把 chapters 落 DB(给 P4 渲染)
2. ⭐ 写 ``Project.status = 'outline_ready'``(让 ``/confirm-outline`` 端点
   能通过状态校验)
3. publish 'outline_ready' SSE
4. interrupt 等用户编辑;resume 后再写 ``Project.status = 'running'``

resume payload 形状::

    {"kind": "outline_confirm",
     "chapters": [...]}    # 用户编辑后的章节;为空/None 表示自动确认
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_outline_to_db, sync_project_status


def _real_run(run_id: int | None) -> bool:
    """run_id > 0 才视为真 DB 路径(CLI 走 -1)。"""
    return run_id is not None and run_id > 0


def _real_project(project_id: int | None) -> bool:
    return project_id is not None and project_id > 0


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    run_id = state.get("run_id")
    chapters = state.get("chapters") or []

    # 1+2. 落 DB,project 进入 outline_ready(CLI 路径跳过)
    if _real_run(run_id):
        try:
            await sync_outline_to_db(run_id, chapters)  # type: ignore[arg-type]
        except Exception:
            # 在测试 / 表缺失场景容错;真生产路径走完整 DB 应能通过
            import structlog

            structlog.get_logger().exception(
                "outline_review_outline_sync_failed", run_id=run_id
            )
    if _real_project(pid):
        await sync_project_status(pid, "outline_ready")

    # 3. SSE 通知前端拉提纲(永远 publish,CLI 也安全:event_bus 包了 try)
    await publish_event(pid, "outline_ready", chapters=chapters)

    # 4. interrupt 暂停;后续由 /confirm-outline → resume_review_task 注入
    payload = interrupt(
        {"kind": "outline_confirm", "current_chapters": chapters}
    )

    # resume 后:Project 回到 running,准备进章节循环
    if _real_project(pid):
        await sync_project_status(pid, "running")

    edited = (payload or {}).get("chapters")
    if edited:
        if _real_run(run_id):
            try:
                await sync_outline_to_db(
                    run_id, edited, replace=True  # type: ignore[arg-type]
                )
            except Exception:
                import structlog

                structlog.get_logger().exception(
                    "outline_review_edited_sync_failed", run_id=run_id
                )
        return {
            "chapters": edited,
            "current_index": 0,
            "_outline_confirmed_chapters": edited,
        }

    return {
        "current_index": 0,
        "_outline_confirmed_chapters": chapters,
    }
