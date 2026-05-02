"""LLM-3 章节可视化建议节点(v10 §4.5.3 / Spec §10.2 ``gen_visuals``)。

读 LLM-2 输出的章节正文,产出 ``{"items":[...]}`` JSON 建议清单,供下游
``merge_chapter`` 模板转换合并。

输入:``state._pending_chapter_text``
输出:``state._pending_visuals_json``(原始 JSON 字符串)

⚠️ M0-4 期间任务清单上写 ``review_chapter.py``,M1-6 (#8) 评估 graph
偏差时按 §10.2 spec 三节点拆分(D-EE 候选,见 ``graph.py`` docstring):
本文件归位 ``gen_visuals.py``;人工审核 interrupt 单独 ``human_review.py``;
``merge_chapter.py`` 仅做模板转换。提示词模块名仍是 ``review_chapter_prompt.py``
(M0-3 任务命名固化)。
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


async def _resolve_api_key(project_id: int, run_id: int | None = None) -> str:
    """⭐ D-C 真快照 + R10 严格失败语义(REVIEW-2 🔴 修复)。

    生产路径(``run_id > 0``):snapshot 缺失 / decrypt 失败 / DB 异常都
    raise(worker 顶层 ``_fail_project_and_run``);**不**回退环境变量,
    防 .env 误注入 ``BID_APP_CLI_API_KEY`` 让 worker 偷换用户真快照。
    CLI 路径(``run_id <= 0``)允许 ``$BID_APP_CLI_API_KEY`` fallback。
    """
    import os

    is_production = run_id is not None and run_id > 0

    encrypted: bytes | None = None
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
    except Exception as e:
        if is_production:
            raise RuntimeError(
                f"db error resolving api_key for project {project_id}: {e}"
            ) from e

    if encrypted is not None:
        try:
            return decrypt_api_key(encrypted)  # type: ignore[name-defined]
        except Exception as e:
            if is_production:
                raise RuntimeError(
                    f"decrypt api_key failed for project {project_id} "
                    f"(master_key 与启动时不一致?R10 检查): {e}"
                ) from e

    if is_production:
        raise RuntimeError(
            f"project {project_id} has no api_key snapshot; did /start succeed?"
        )

    cli_key = os.environ.get("BID_APP_CLI_API_KEY")
    if cli_key:
        return cli_key
    raise RuntimeError(
        f"project {project_id} has no api_key snapshot; did /start succeed? "
        "(or set BID_APP_CLI_API_KEY for CLI mode)"
    )


async def _resolve_user_id(project_id: int) -> int:
    """``Project.api_key_owner`` 是 ``Mapped[int | None]``,行存在但字段
    NULL 时返 0(REVIEW-2 🟡 #3 fix)。"""
    try:
        from ...models import Project  # type: ignore[attr-defined]

        async with session_factory() as s:
            row = await s.execute(
                select(Project.api_key_owner).where(Project.id == project_id)
            )
            return row.scalar_one_or_none() or 0
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
            "gen_visuals_empty_text",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}

    api_key = await _resolve_api_key(project_id, run_id=run_id)
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
            "gen_visuals_failed",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}
