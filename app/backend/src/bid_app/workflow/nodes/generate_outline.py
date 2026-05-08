"""LLM-1 提纲生成节点(v10 §4.3 / Spec §10.2 / D-K)。

读取 ``state.tech_spec_md`` / ``scoring_md`` / ``template_md``,调
``call_llm_json`` 拿到 outline JSON 字符串,落到 ``state._outline_json``
临时载体,下游 ``parse_outline`` 解析成结构化 chapters。
"""
from __future__ import annotations

import structlog
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...services.llm import call_llm_json
from ..prompts.outline_prompt import build_messages
from ..resolve import resolve_models
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
        from ...core.crypto import decrypt_api_key
        from ...models import Project

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
            return decrypt_api_key(encrypted)
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
        from ...models import Project

        async with session_factory() as s:
            row = await s.execute(
                select(Project.api_key_owner).where(Project.id == project_id)
            )
            return row.scalar_one_or_none() or 0
    except Exception:
        return 0


async def run(state: WorkflowState) -> dict[str, str]:
    project_id = state["project_id"]
    run_id = state.get("run_id")

    api_key = await _resolve_api_key(project_id, run_id=run_id)
    user_id = await _resolve_user_id(project_id)

    messages = build_messages(
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        template_md=state.get("template_md", ""),
    )

    await publish_event(project_id, "outline_started")
    models = await resolve_models(project_id)
    _parsed, sr = await call_llm_json(
        model=models.outline_model,
        messages=messages,
        api_key=api_key,
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        timeout_seconds=settings.llm_outline_timeout_seconds,
    )

    return {"_outline_json": sr.text}
