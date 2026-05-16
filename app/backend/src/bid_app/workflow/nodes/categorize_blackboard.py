"""结构化实体桶节点 (Phase 1A, 2026-05-16)。

时序:``material_understanding_review`` 用户 pass / skip 之后、
``generate_outline`` 之前。用户在材料理解页 revise 时**不跑**(LLM-0 重读
材料理解 → 用户再 pass / skip → 才跑此节点),节省 categorize LLM 调用。

读 ``state.blackboard_excerpt``(extract_documents 节点写好的清洗 HTML),
调 LLM 拆 10 桶,落 ``state.blackboard_entities`` + ``Project.blackboard_entities``。

异常:LLM 调用失败 / JSON 不合法 → 抛上去(resume_review_task 走
``except Exception → _fail_project_and_run`` 把项目标 failed)。Phase 2
之前下游 LLM-1/2 还在用截断 markdown,本节点失败不影响 outline / chapter
生成,只是丢失「结构化检索源」这层增强;Phase 2 接 tool calling 后再
考虑加回退路径。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from ...config import settings
from ...db import session_factory
from ...services.llm import LLMRetryFailed, LLMTimeoutExceeded, call_llm_json
from ..prompts import categorize_blackboard as prompt
from ..resolve import resolve_api_key, resolve_models, resolve_user_id
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def _save_entities_to_project(
    project_id: int, entities: dict[str, list[dict[str, Any]]]
) -> None:
    """把桶 JSON 写到 ``Project.blackboard_entities`` 列。失败仅 log,不
    阻塞工作流(state 里仍带,下游 prompt 优先从 state 拿)。"""
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE projects SET blackboard_entities=CAST(:e AS JSONB) "
                    "WHERE id=:i"
                ),
                {"e": _to_jsonb_text(entities), "i": project_id},
            )
            await s.commit()
    except Exception:
        log.exception(
            "blackboard_entities_db_update_failed",
            project_id=project_id,
        )


def _to_jsonb_text(entities: dict[str, list[dict[str, Any]]]) -> str:
    """psycopg 无法直接绑定 dict 给 JSONB 列时的兜底:序列化为 JSON
    字符串,SQL 里 ``CAST(:e AS JSONB)``。比走 ORM 简单稳。"""
    import json as _json

    return _json.dumps(entities, ensure_ascii=False)


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = int(state["project_id"])
    run_id = state.get("run_id")
    blackboard_excerpt = state.get("blackboard_excerpt") or ""

    if not blackboard_excerpt.strip():
        log.warning(
            "categorize_blackboard_empty_excerpt",
            project_id=project_id,
        )
        empty: dict[str, list[dict[str, Any]]] = {
            b: [] for b in prompt.ENTITY_BUCKETS
        }
        return {"blackboard_entities": empty}

    api_key = await resolve_api_key(project_id, run_id=run_id)
    user_id = await resolve_user_id(project_id)
    models = await resolve_models(project_id)

    messages = prompt.build_messages(blackboard_excerpt=blackboard_excerpt)

    try:
        parsed, _ = await call_llm_json(
            model=models.outline_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id if isinstance(run_id, int) and run_id > 0 else None,
            timeout_seconds=settings.llm_outline_timeout_seconds,
            # 10 桶完整 JSON 体积可观,留 16k 防截断 (与 generate_outline 同档)
            max_tokens=16384,
        )
    except (LLMRetryFailed, LLMTimeoutExceeded):
        log.exception(
            "categorize_blackboard_llm_failed", project_id=project_id
        )
        raise

    entities = prompt.normalize_entities(parsed)
    total = sum(len(v) for v in entities.values())
    log.info(
        "categorize_blackboard_done",
        project_id=project_id,
        bucket_counts={k: len(v) for k, v in entities.items()},
        total_entries=total,
    )

    await _save_entities_to_project(project_id, entities)
    await publish_event(
        project_id,
        "blackboard_entities_ready",
        bucket_counts={k: len(v) for k, v in entities.items()},
    )

    return {"blackboard_entities": entities}
