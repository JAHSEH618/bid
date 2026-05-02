"""LLM-3 章节可视化建议节点(对应 v10 §4.5.3 / Spec §10.2 ``gen_visuals``)。

⚠️ 命名说明:任务清单把本文件标作 "review_chapter"(章节审核),实际承担
v10 §4.5.3 的 LLM-3 可视化建议职责——读 LLM-2 输出的章节正文,产出
``{"items":[...]}`` JSON 建议清单,供下游 ``merge_chapter`` 模板转换合并。
"审核"语义另由 P5 ``human_review`` interrupt 节点承担(参见 ``merge_chapter``)。

输入:``state._pending_chapter_text``
输出:``state._pending_visuals_json``(原始 JSON 字符串)
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...services.llm import call_llm_json
from ..prompts.review_chapter_prompt import build_messages
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def _resolve_api_key(project_id: int) -> str:
    """⭐ D-C 真快照 + CLI fallback($BID_APP_CLI_API_KEY)。"""
    import os

    try:
        from ...core.crypto import decrypt_api_key  # type: ignore[attr-defined]
        from ...models import Project  # type: ignore[attr-defined]

        async with session_factory() as s:
            row = await s.execute(
                select(Project.encrypted_api_key_snapshot).where(
                    Project.id == project_id
                )
            )
            encrypted = row.scalar_one_or_none()
        if encrypted is not None:
            return decrypt_api_key(encrypted)
    except Exception:
        pass

    cli_key = os.environ.get("BID_APP_CLI_API_KEY")
    if cli_key:
        return cli_key
    raise RuntimeError(
        f"project {project_id} has no api_key snapshot; did /start succeed? "
        "(or set BID_APP_CLI_API_KEY for CLI mode)"
    )


async def _resolve_user_id(project_id: int) -> int:
    try:
        from ...models import Project  # type: ignore[attr-defined]

        async with session_factory() as s:
            row = await s.execute(
                select(Project.api_key_owner).where(Project.id == project_id)
            )
            return row.scalar_one()
    except Exception:
        return 0


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = state["project_id"]
    run_id = state.get("run_id")
    idx = state["current_index"]
    chapter = state["chapters"][idx]
    chapter_text = state.get("_pending_chapter_text", "")

    if not chapter_text.strip():
        log.warning(
            "review_chapter_empty_text",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}

    api_key = await _resolve_api_key(project_id)
    user_id = await _resolve_user_id(project_id)

    messages = build_messages(
        chapter_title=chapter.get("title", ""),
        chapter_body_md=chapter_text,
    )

    try:
        _parsed, sr = await call_llm_json(
            model=settings.llm3_visuals_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            timeout_seconds=60,
            temperature=0.4,
        )
        await publish_event(
            project_id, "chapter_visuals_ready", chapter_index=idx
        )
        return {"_pending_visuals_json": sr.text}
    except Exception:
        # ⭐ LLM-3 是非关键路径(只是补可视化),失败不能中断章节流程
        log.exception(
            "review_chapter_visuals_failed",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}
