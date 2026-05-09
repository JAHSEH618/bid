"""LLM-2 章节正文生成节点(§11.2)。

关键:api_key 不进 state(D-C),运行时从 ``Project.encrypted_api_key_snapshot``
读后解密。

⭐ D-AU:LLMRetryFailed / Timeout 后包成 ``ChapterGenerationFailed`` 抛出,
worker task 据此把 project 切 ``awaiting_review`` 而不是 ``failed``——只是
当前章节失败,工作流暂停等用户 ``/retry``。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
from ..resolve import resolve_chapter_model
from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db


async def _resolve_api_key(project_id: int, run_id: int | None = None) -> str:
    """⭐ D-C 真快照 + R10 严格失败语义(REVIEW-2 🔴 修复)。

    生产路径(``run_id > 0``):
      - DB 查询失败 → raise(worker 顶层 ``_fail_project_and_run`` 捕获)
      - snapshot 缺失 → raise(说明 /start 路径 commit 漏写)
      - decrypt 失败 → raise(master_key 与 .env 不一致;R10 不允许 silent
        降级,运维必须看到错)
      - **不**回退到 ``$BID_APP_CLI_API_KEY``(防 .env 误注入 fallback key
        让 worker 用 env 替用户真快照,违反 D-C / FR-7.4 / R10)

    CLI 路径(``run_id is None`` 或 ``run_id <= 0``):
      - 任何失败都允许 fallback 到 ``$BID_APP_CLI_API_KEY``;
        无 env key 时仍 raise
    """
    import os

    is_production = run_id is not None and run_id > 0

    encrypted: bytes | None = None
    try:
        from ...core.crypto import decrypt_api_key
        from ...models import Project

        async with session_factory() as s:
            row = await s.execute(
                select(Project.encrypted_api_key_snapshot).where(Project.id == project_id)
            )
            encrypted = row.scalar_one_or_none()
    except Exception as e:
        if is_production:
            raise RuntimeError(f"db error resolving api_key for project {project_id}: {e}") from e
        # CLI:吞异常,继续走 fallback

    if encrypted is not None:
        try:
            return decrypt_api_key(encrypted)
        except Exception as e:
            if is_production:
                raise RuntimeError(
                    f"decrypt api_key failed for project {project_id} "
                    f"(master_key 与启动时不一致?R10 检查): {e}"
                ) from e
            # CLI 路径才允许 fallback

    if is_production:
        raise RuntimeError(f"project {project_id} has no api_key snapshot; did /start succeed?")

    cli_key = os.environ.get("BID_APP_CLI_API_KEY")
    if cli_key:
        return cli_key
    raise RuntimeError(
        f"project {project_id} has no api_key snapshot; did /start succeed? "
        "(or set BID_APP_CLI_API_KEY for CLI mode)"
    )


async def _resolve_user_id(project_id: int) -> int:
    """token_usage 记账要 user_id,用 ``api_key_owner``(快照时锁定的启动者)。

    ``Project.api_key_owner`` 是 ``Mapped[int | None]``,行存在但字段 NULL
    时返 0(REVIEW-2 🟡 #3 fix:原来用 ``scalar_one()`` 在 NULL 时返 None,
    后续 ``int(None)`` 静默 skip 记账)。
    """
    try:
        from ...models import Project

        async with session_factory() as s:
            row = await s.execute(select(Project.api_key_owner).where(Project.id == project_id))
            return row.scalar_one_or_none() or 0
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


async def _safe_sync_chapter(run_id: int | None, index: int, **fields: Any) -> None:
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


def _chapter_generation_limit() -> int:
    """正文 LLM 并发上限。用户要求最多 3,环境变量可降不可升。"""
    return max(1, min(3, int(settings.max_concurrent_chapter_generations or 1)))


async def _load_prefetched_body(
    run_id: int | None,
    index: int,
    *,
    retry_count: int,
    revision_feedback: str,
) -> str | None:
    """取提前生成好的正文草稿。

    只服务首次生成(retry_count=0 且无反馈)。重写路径必须重新走 LLM,
    避免把上一轮用户不满意的内容误当缓存复用。
    """
    if not _real_run(run_id) or retry_count > 0 or revision_feedback.strip():
        return None
    try:
        async with session_factory() as s:
            row = await s.execute(
                sa.text(
                    "SELECT final_text FROM chapters "
                    "WHERE run_id=:r AND index=:i AND status='pending' "
                    "AND retry_count=0 AND NULLIF(final_text, '') IS NOT NULL"
                ),
                {"r": run_id, "i": index},
            )
            text = row.scalar_one_or_none()
            return str(text) if text else None
    except Exception:
        import structlog

        structlog.get_logger().exception(
            "write_chapter_prefetch_read_failed", run_id=run_id, index=index
        )
        return None


async def _prefetch_candidate_indices(
    run_id: int | None,
    *,
    current: int,
    total: int,
    limit: int,
) -> list[int]:
    """返回当前章之后最多 limit-1 个可提前生成正文的章节索引。"""
    if not _real_run(run_id) or limit <= 1:
        return []
    upper = min(total, current + limit)
    if current + 1 >= upper:
        return []
    try:
        async with session_factory() as s:
            rows = await s.execute(
                sa.text(
                    "SELECT index FROM chapters "
                    "WHERE run_id=:r AND index > :current AND index < :upper "
                    "AND status='pending' AND retry_count=0 "
                    "AND NULLIF(final_text, '') IS NULL "
                    "ORDER BY index ASC"
                ),
                {"r": run_id, "current": current, "upper": upper},
            )
            return [int(row[0]) for row in rows.fetchall()]
    except Exception:
        import structlog

        structlog.get_logger().exception(
            "write_chapter_prefetch_candidates_failed",
            run_id=run_id,
            current=current,
        )
        return []


async def _prefetch_chapter_body(
    state: WorkflowState,
    index: int,
    *,
    api_key: str,
    user_id: int,
) -> None:
    """提前生成后续章节正文,缓存到 chapters.final_text / chapter_versions。

    该预生成不进入审核态,也不发布 token,只是让后续章节点击"生成本章"后
    能跳过 LLM-2 正文阶段,继续补图表并进入人工审核。
    """
    run_id = state.get("run_id")
    project_id = state["project_id"]
    if not _real_run(run_id):
        return

    import structlog

    log_local = structlog.get_logger()
    version_id: int | None = None
    try:
        await _safe_sync_chapter(
            run_id,
            index,
            status="generating",
            processing_started_at=datetime.now(UTC),
        )
        from ..sync import save_chapter_version

        version_id = await save_chapter_version(
            run_id,  # type: ignore[arg-type]
            index,
            "",
        )

        chapter = state["chapters"][index]
        messages = build_messages(
            chapter=chapter,
            tech_spec_md=state.get("tech_spec_md", ""),
            scoring_md=state.get("scoring_md", ""),
            revision_feedback="",
            retry_count=0,
            previous_text="",
        )
        chapter_model = await resolve_chapter_model(project_id, run_id, index, chapter)
        result = await call_llm_stream(
            model=chapter_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            chapter_index=None,
            temperature=0.6,
        )

        from ..postprocess import postprocess_chapter_markdown
        from ..sync import flush_chapter_partial

        final_text = postprocess_chapter_markdown(result.text)
        await flush_chapter_partial(
            run_id,  # type: ignore[arg-type]
            index,
            version_id,
            final_text,
        )
        await _safe_sync_chapter(
            run_id,
            index,
            status="pending",
            processing_started_at=None,
            last_error=None,
        )
        await publish_event(project_id, "chapter_prefetched", chapter_index=index)
    except asyncio.CancelledError:
        await _safe_sync_chapter(
            run_id,
            index,
            status="pending",
            processing_started_at=None,
        )
        raise
    except Exception as e:
        log_local.exception(
            "write_chapter_prefetch_failed",
            project_id=project_id,
            run_id=run_id,
            chapter_index=index,
        )
        await _safe_sync_chapter(
            run_id,
            index,
            status="pending",
            processing_started_at=None,
            last_error=f"prefetch failed: {e}",
        )


async def run(state: WorkflowState) -> dict[str, Any]:
    current = state["current_index"]
    chapter = state["chapters"][current]
    run_id = state["run_id"]
    project_id = state["project_id"]

    api_key = await _resolve_api_key(project_id, run_id=run_id)
    user_id = await _resolve_user_id(project_id)
    retry_count = state.get("retry_count", 0)
    revision_feedback = state.get("revision_feedback") or ""

    cached_body = await _load_prefetched_body(
        run_id,
        current,
        retry_count=retry_count,
        revision_feedback=revision_feedback,
    )
    if cached_body:
        await _safe_sync_chapter(
            run_id,
            current,
            status="generating",
            processing_started_at=datetime.now(UTC),
        )
        await publish_event(project_id, "chapter_started", chapter_index=current)
        return {"_pending_chapter_text": cached_body}

    # ⭐ D-BF:切 generating 同时写 processing_started_at,让
    # cron `cleanup_stale_chapters` 在 worker 进程被 SIGKILL/OOM 直接死时
    # 也能扫到这个章节回滚状态
    await _safe_sync_chapter(
        run_id,
        current,
        status="generating",
        processing_started_at=datetime.now(UTC),
    )
    await publish_event(project_id, "chapter_started", chapter_index=current)

    # ⭐ R-18:retry / revise 时(retry_count > 0)拉上一轮正文给 LLM 做
    # patch 修订。**必须在 save_chapter_version pre-create 之前查**——
    # 否则查到的"latest"是新创建的空占位行。
    previous_text: str = ""
    if retry_count > 0 and _real_run(run_id):
        try:
            from ..sync import get_latest_chapter_version_text

            previous_text = await get_latest_chapter_version_text(run_id, current) or ""
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_previous_text_fetch_failed",
                run_id=run_id,
                index=current,
            )

    # ⭐ R-14:**预创建 ChapterVersion 占位**(空 body),拿到 version_id
    # 给 periodic flush 用。流式生成期间 partial 写到这一行的 body_markdown,
    # 流结束后同一行被 final UPDATE 成完整正文(idempotent)。
    version_id: int | None = None
    if _real_run(run_id):
        try:
            from ..sync import save_chapter_version

            version_id = await save_chapter_version(
                run_id,
                current,
                "",  # 空 body 占位,流式期间被 flush_chapter_partial 覆盖
                feedback_in=revision_feedback or None,
            )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_version_pre_create_failed",
                run_id=run_id,
                index=current,
            )

    messages = build_messages(
        chapter=chapter,
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        revision_feedback=revision_feedback,
        retry_count=retry_count,
        previous_text=previous_text,  # ⭐ R-18
    )

    # ⭐ R-14:periodic flush 回调 —— call_llm_stream 内部每 100 chunks /
    # ≥1s 触发一次,把累积 partial 写 chapters.final_text +
    # chapter_versions.body_markdown(同事务防漂移)。回调内部异常 swallow
    # 不打断 LLM 流,_real_run 守护让 CLI 路径自动跳过 DB 写。
    async def _on_partial(partial_text: str) -> None:
        if not _real_run(run_id):
            return
        try:
            from ..sync import flush_chapter_partial

            await flush_chapter_partial(
                run_id,
                current,
                version_id,
                partial_text,
            )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_partial_flush_failed",
                run_id=run_id,
                index=current,
            )

    chapter_model = await resolve_chapter_model(project_id, run_id, current, chapter)
    prefetch_tasks: list[asyncio.Task[None]] = []
    if retry_count == 0 and not revision_feedback.strip():
        prefetch_indices = await _prefetch_candidate_indices(
            run_id,
            current=current,
            total=len(state.get("chapters") or []),
            limit=_chapter_generation_limit(),
        )
        prefetch_tasks = [
            asyncio.create_task(
                _prefetch_chapter_body(
                    state,
                    index,
                    api_key=api_key,
                    user_id=user_id,
                )
            )
            for index in prefetch_indices
        ]

    try:
        result = await call_llm_stream(
            model=chapter_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            chapter_index=current,
            temperature=0.6,
            on_partial=_on_partial,  # ⭐ R-14
        )
    except (TimeoutError, LLMRetryFailed, LLMTimeoutExceeded) as e:
        for task in prefetch_tasks:
            task.cancel()
        if prefetch_tasks:
            await asyncio.gather(*prefetch_tasks, return_exceptions=True)
        # D-BG:call_llm_stream 总超时已包成 LLMTimeoutExceeded,这里同时
        # catch asyncio.TimeoutError 是兜底。
        await _safe_sync_chapter(
            run_id,
            current,
            status="failed",
            last_error=str(e),
            processing_started_at=None,
        )
        await publish_event(project_id, "chapter_failed", chapter_index=current, reason=str(e))
        # ⭐ D-AU:用语义化异常,worker task 据此把 project 切 awaiting_review
        raise ChapterGenerationFailed(
            str(e),
            chapter_index=current,
            chapter_id=await _resolve_chapter_id(run_id, current),
        ) from e
    except Exception:
        for task in prefetch_tasks:
            task.cancel()
        if prefetch_tasks:
            await asyncio.gather(*prefetch_tasks, return_exceptions=True)
        raise
    if prefetch_tasks:
        await asyncio.gather(*prefetch_tasks, return_exceptions=True)

    # ⭐ R-17 + R-19:LLM 出来的 markdown 偶尔段落紧挨 / mermaid block 里夹
    # 装饰色 `style X fill:#xxx`(会 override 前端白底主题)。统一过 postprocess
    # 入口兜底:strip mermaid 装饰 → normalize 段落空行。**只对 final 正文**
    # 处理,不动 partial flush(流式中间态,反复处理会让用户看到段落跳变)
    from ..postprocess import postprocess_chapter_markdown

    final_text = postprocess_chapter_markdown(result.text)

    # ⭐ R-14:final flush 完整正文到 DB(_on_partial 末尾触发的 flush 在
    # 真实路径下已包含完整 text,本 UPDATE 是兜底/语义闭合 + 写 normalize 后版本)
    if _real_run(run_id) and version_id is not None:
        try:
            from ..sync import flush_chapter_partial

            await flush_chapter_partial(
                run_id,
                current,
                version_id,
                final_text,
            )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_final_flush_failed",
                run_id=run_id,
                index=current,
            )

    return {"_pending_chapter_text": final_text}
