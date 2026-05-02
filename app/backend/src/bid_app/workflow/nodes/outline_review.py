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


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    run_id = state.get("run_id")

    # 1+2. 落 DB,project 进入 outline_ready
    if run_id is not None:
        await sync_outline_to_db(run_id, state["chapters"])
    await sync_project_status(pid, "outline_ready")

    # 3. SSE 通知前端拉提纲
    await publish_event(pid, "outline_ready", chapters=state["chapters"])

    # 4. interrupt 暂停;后续由 /confirm-outline → resume_review_task 注入
    payload = interrupt(
        {"kind": "outline_confirm", "current_chapters": state["chapters"]}
    )

    # resume 后:Project 回到 running,准备进章节循环
    await sync_project_status(pid, "running")

    edited = (payload or {}).get("chapters")
    if edited:
        if run_id is not None:
            await sync_outline_to_db(run_id, edited, replace=True)
        return {
            "chapters": edited,
            "current_index": 0,
            "_outline_confirmed_chapters": edited,
        }

    return {
        "current_index": 0,
        "_outline_confirmed_chapters": state["chapters"],
    }
