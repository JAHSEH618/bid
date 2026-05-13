"""LLM-0 材料理解节点 (PR-M8-1)。

读 ``state.blackboard_excerpt`` (PR-M7-3 黑板节点写入),调 LLM-0 出
结构化 JSON,落到 ``state.material_understanding``。

revise 路径:``state.revision_feedback`` 非空时,把它注入到 prompt
的 REVISION_TEMPLATE,让 LLM-0 做有针对性的重写。

异常:LLM 调用失败 / JSON 不合法时,把项目状态切 awaiting_review,
让用户决定 retry。
"""

from __future__ import annotations

from typing import Any

import structlog

from ...services.llm import LLMRetryFailed, LLMTimeoutExceeded, call_llm_json
from ..prompts import material_understanding as prompt
from ..resolve import resolve_api_key, resolve_models, resolve_user_id
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = int(state["project_id"])
    run_id = state.get("run_id")
    blackboard_excerpt = state.get("blackboard_excerpt") or ""
    revision_feedback = state.get("revision_feedback") or ""

    api_key = await resolve_api_key(project_id, run_id=run_id)
    user_id = await resolve_user_id(project_id)
    models = await resolve_models(project_id)

    messages = prompt.build_messages(
        blackboard_excerpt=blackboard_excerpt,
        revision_feedback=revision_feedback,
    )

    try:
        parsed, _ = await call_llm_json(
            model=models.outline_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id if isinstance(run_id, int) and run_id > 0 else None,
            timeout_seconds=180,
        )
    except (LLMRetryFailed, LLMTimeoutExceeded):
        log.exception(
            "material_understanding_llm_failed", project_id=project_id
        )
        raise

    if not isinstance(parsed, dict):
        log.warning(
            "material_understanding_unexpected_type",
            project_id=project_id,
            type=type(parsed).__name__,
        )
        parsed = {}

    await publish_event(
        project_id, "material_understanding_ready", payload=parsed
    )

    # 清空 revision_feedback,避免下一轮 LLM-1 误读
    return {
        "material_understanding": parsed,
        "revision_feedback": "",
    }
