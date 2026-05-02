"""LLM-2 章节正文生成节点(§11.2)。

关键:api_key 不进 state(D-C),运行时从 ``Project.encrypted_api_key_snapshot``
读后解密。

⭐ D-AU:LLMRetryFailed / Timeout 后包成 ``ChapterGenerationFailed`` 抛出,
worker task 据此把 project 切 ``awaiting_review`` 而不是 ``failed``——只是
当前章节失败,工作流暂停等用户 ``/retry``。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...services.llm import (
    ChapterGenerationFailed,
    LLMRetryFailed,
    LLMTimeoutExceeded,
    call_llm_stream,
)
from ..prompts.write_chapter_prompt import build_messages
from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db


async def _resolve_api_key(project_id: int) -> str:
    """⭐ D-C 真快照:直接从 ``Project.encrypted_api_key_snapshot`` 读,
    与用户当前的 ApiKey 表完全解耦。

    ⭐ CLI fallback:DB / models / crypto 任何一层不可用时,回退到
    ``$BID_APP_CLI_API_KEY``(M0 ``run_local`` 走此路径)。
    """
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
    """token_usage 记账要 user_id,用 ``api_key_owner``(快照时锁定的启动者)。

    CLI fallback:DB 不可用时返回 0(``record_token_usage`` 是 stub,值随便)。
    """
    try:
        from ...models import Project  # type: ignore[attr-defined]

        async with session_factory() as s:
            row = await s.execute(
                select(Project.api_key_owner).where(Project.id == project_id)
            )
            return row.scalar_one()
    except Exception:
        return 0


async def _resolve_chapter_id(run_id: int, index: int) -> int | None:
    """D-AU:抛 ``ChapterGenerationFailed`` 时一并带上 chapter_id。"""
    if run_id is None or run_id <= 0:
        return None
    try:
        async with session_factory() as s:
            row = await s.execute(
                sa.text("SELECT id FROM chapters WHERE run_id=:r AND index=:i"),
                {"r": run_id, "i": index},
            )
            return row.scalar_one_or_none()
    except Exception:
        return None


def _real_run(run_id: int | None) -> bool:
    """run_id > 0 才视为真 DB 路径(CLI 走 -1)。"""
    return run_id is not None and run_id > 0


async def _safe_sync_chapter(
    run_id: int | None, index: int, **fields: Any
) -> None:
    """sync_chapter_to_db 包装:run_id <= 0 跳过,DB 异常吞掉(M0 CLI 友好)。"""
    if not _real_run(run_id):
        return
    try:
        await sync_chapter_to_db(run_id, index, **fields)  # type: ignore[arg-type]
    except Exception:
        import structlog

        structlog.get_logger().exception(
            "write_chapter_sync_failed", run_id=run_id, index=index, fields=fields
        )


async def run(state: WorkflowState) -> dict[str, Any]:
    current = state["current_index"]
    chapter = state["chapters"][current]
    run_id = state["run_id"]
    project_id = state["project_id"]

    api_key = await _resolve_api_key(project_id)

    # ⭐ D-BF:切 generating 同时写 processing_started_at,让
    # cron `cleanup_stale_chapters` 在 worker 进程被 SIGKILL/OOM 直接死时
    # 也能扫到这个章节回滚状态
    await _safe_sync_chapter(
        run_id,
        current,
        status="generating",
        processing_started_at=datetime.now(timezone.utc),
    )
    await publish_event(project_id, "chapter_started", chapter_index=current)

    messages = build_messages(
        chapter=chapter,
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        revision_feedback=state.get("revision_feedback", ""),
        retry_count=state.get("retry_count", 0),
    )

    try:
        result = await call_llm_stream(
            model=settings.llm2_chapter_model,
            messages=messages,
            api_key=api_key,
            user_id=await _resolve_user_id(project_id),
            project_id=project_id,
            run_id=run_id,
            chapter_index=current,
            temperature=0.6,
        )
    except (LLMRetryFailed, LLMTimeoutExceeded, asyncio.TimeoutError) as e:
        # D-BG:call_llm_stream 总超时已包成 LLMTimeoutExceeded,这里同时
        # catch asyncio.TimeoutError 是兜底。
        await _safe_sync_chapter(
            run_id,
            current,
            status="failed",
            last_error=str(e),
            processing_started_at=None,
        )
        await publish_event(
            project_id, "chapter_failed", chapter_index=current, reason=str(e)
        )
        # ⭐ D-AU:用语义化异常,worker task 据此把 project 切 awaiting_review
        raise ChapterGenerationFailed(
            str(e),
            chapter_index=current,
            chapter_id=await _resolve_chapter_id(run_id, current),
        ) from e

    # 把生成的章节正文保存为新版本(走 sync.save_chapter_version,自动取
    # 下一个 version 号);CLI / 表缺失时 sync 内部已 log 容错
    if _real_run(run_id):
        try:
            from ..sync import save_chapter_version

            await save_chapter_version(
                run_id,
                current,
                result.text,
                feedback_in=state.get("revision_feedback") or None,
            )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_version_save_failed",
                run_id=run_id,
                index=current,
            )

    return {"_pending_chapter_text": result.text}
