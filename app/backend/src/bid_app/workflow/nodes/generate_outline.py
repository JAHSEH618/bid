"""LLM-1 提纲生成节点(v10 §4.3 / Spec §10.2 / D-K)。

读取 ``state.tech_spec_md`` / ``scoring_md`` / ``template_md``,调
``call_llm_json`` 拿到 outline JSON 字符串,落到 ``state._outline_json``
临时载体,下游 ``parse_outline`` 解析成结构化 chapters。
"""
from __future__ import annotations

import structlog
from sqlalchemy import select

from ...config import settings
from ...core.crypto import decrypt_api_key  # type: ignore[attr-defined]
from ...db import session_factory
from ...services.llm import call_llm_json
from ..prompts.outline_prompt import build_messages
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def _resolve_api_key(project_id: int) -> str:
    """⭐ D-C 真快照:从 ``Project.encrypted_api_key_snapshot`` 读后解密。"""
    from ...models import Project  # type: ignore[attr-defined]

    async with session_factory() as s:
        row = await s.execute(
            select(Project.encrypted_api_key_snapshot).where(Project.id == project_id)
        )
        encrypted = row.scalar_one_or_none()
    if encrypted is None:
        raise RuntimeError(
            f"project {project_id} has no api_key snapshot; did /start succeed?"
        )
    return decrypt_api_key(encrypted)


async def _resolve_user_id(project_id: int) -> int:
    from ...models import Project  # type: ignore[attr-defined]

    async with session_factory() as s:
        row = await s.execute(
            select(Project.api_key_owner).where(Project.id == project_id)
        )
        return row.scalar_one()


async def run(state: WorkflowState) -> dict[str, str]:
    project_id = state["project_id"]
    run_id = state.get("run_id")

    api_key = await _resolve_api_key(project_id)
    user_id = await _resolve_user_id(project_id)

    messages = build_messages(
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        template_md=state.get("template_md", ""),
    )

    await publish_event(project_id, "outline_started")
    _parsed, sr = await call_llm_json(
        model=settings.llm1_outline_model,
        messages=messages,
        api_key=api_key,
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        timeout_seconds=120,
    )

    return {"_outline_json": sr.text}
