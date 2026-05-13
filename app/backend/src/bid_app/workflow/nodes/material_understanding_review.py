"""材料理解人工评审 interrupt 节点 (PR-M8-1)。

工作流停在这里,等用户在前端 ``MaterialUnderstandingPage`` 上点
pass / revise / skip。Resume payload 形状::

    {"kind": "material_understanding",
     "decision": "pass" | "revise" | "skip",
     "feedback": "用户给 LLM-0 的修订意见"}

revise 分支由上层 graph 的 conditional edge 把控:本节点只把决策与
反馈写回 state,真正的环回 (回到 material_understanding 节点) 由
``_route_after_material_review`` 决定。

新增 Project.status = ``awaiting_material_understanding``:让前端从
GET /projects/{id} 就能知道该跳哪个页面。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status


def _real_project(project_id: int | None) -> bool:
    return project_id is not None and project_id > 0


async def run(state: WorkflowState) -> dict[str, Any]:
    pid_raw = state.get("project_id")
    material_understanding = state.get("material_understanding") or {}

    if _real_project(pid_raw):
        pid = int(pid_raw or 0)
        await sync_project_status(pid, "awaiting_material_understanding")
        await publish_event(
            pid,
            "material_understanding_review",
            payload=material_understanding,
        )

    payload = interrupt(
        {
            "kind": "material_understanding",
            "current": material_understanding,
        }
    )

    if _real_project(pid_raw):
        await sync_project_status(int(pid_raw or 0), "running")

    decision = (payload or {}).get("decision") or "pass"
    feedback = (payload or {}).get("feedback") or ""

    return {
        "_material_review_decision": decision,
        "_material_review_feedback": feedback,
        # revise 路径:把 feedback 落到 revision_feedback,下一轮 material_understanding 用
        "revision_feedback": feedback if decision == "revise" else "",
    }
