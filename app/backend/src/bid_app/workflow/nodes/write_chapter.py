"""LLM-2 章节正文生成节点(§11.2)。

关键:api_key 不进 state(D-C),运行时从 ``Project.encrypted_api_key_snapshot``
读后解密。

⭐ D-AU:LLMRetryFailed / Timeout 后包成 ``ChapterGenerationFailed`` 抛出,
worker task 据此把 project 切 ``awaiting_review`` 而不是 ``failed``——只是
当前章节失败,工作流暂停等用户 ``/retry``。

⭐ D-EG (2026-05-18):按 ``chapter.chapter_type`` 分流:
- ``image_only`` / ``table_only`` → 调 ``renderers.render`` 直接生成
  Markdown 骨架,**不调 LLM-2**
- ``module`` / ``principle`` / ``architecture`` / ``meeting`` → 选对应
  ``write_*_prompt.SYSTEM`` 作为 system_override 调 LLM-2
- ``normal`` / 未知 → 走默认 ``write_chapter_prompt.LLM2_SYSTEM`` 兜底
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...services.blackboard_retrieval import (
    SEARCH_BLACKBOARD_TOOL,
    make_blackboard_tool_handler,
)
from ...services.embeddings import embed_one
from ...services.llm import (
    ChapterGenerationFailed,
    LLMRetryFailed,
    LLMTimeoutExceeded,
    call_llm_stream,
    call_llm_stream_with_tools,
)
from ..prompts import (
    write_architecture_prompt,
    write_meeting_prompt,
    write_module_prompt,
    write_principle_prompt,
)
from ..prompts.write_chapter_prompt import build_messages
from ..renderers import render as render_template_md
from ..resolve import resolve_chapter_model, resolve_models
from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db

# D-EG:chapter_type → 对应的 system prompt 常量(覆盖默认 LLM2_SYSTEM)
_SYSTEM_BY_TYPE: dict[str, str] = {
    "module": write_module_prompt.SYSTEM,
    "principle": write_principle_prompt.SYSTEM,
    "architecture": write_architecture_prompt.SYSTEM,
    "meeting": write_meeting_prompt.SYSTEM,
}


# 每种 chapter_type 在 user content 末尾追加的"必须遵守"短指令(防止 system
# 内长 prompt 被 LLM 中段稀释)。
_USER_DIRECTIVE_BY_TYPE: dict[str, str] = {
    "module": (
        "## 本章硬约束(再次提醒)\n"
        "- 三段式锚点 `### 技术实现` / `### 关键适配` / `### 典型业务流程` 必须依次出现\n"
        "- 每个流程必含 `流程目标` / `处理步骤` / `关键控制点` 三关键词\n"
        "- 每个流程末尾**单独一行**写 `对应时序图:<流程名>`(完整与流程名一致)"
    ),
    "principle": (
        "## 本章硬约束(再次提醒)\n"
        "- 原则条数与名称严格对齐 ``required_anchors``,不增不减不改名\n"
        "- 每条编号用 `1、` `2、` 等(全角顿号)"
    ),
    "architecture": (
        "## 本章硬约束(再次提醒)\n"
        "- 所有 ``required_anchors`` 层名 100% 必须在正文中出现\n"
        "- 章末**单独一行**写 `对应架构图:总体架构`(完全照抄,触发图表生成)"
    ),
    "meeting": (
        "## 本章硬约束(再次提醒)\n"
        "- 每个会议描述必须含 `会议目标` / `日期与时间` / `参加人员` / `主要议程及责任` 四要素\n"
        "- 四要素各占一行,使用全角冒号"
    ),
}


_TEMPLATE_TYPES = {"image_only", "table_only"}


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
                    "WHERE run_id=:r AND index=:i AND status IN ('pending','generating') "
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


async def _prefetch_chapter_body(
    state: WorkflowState,
    index: int,
    *,
    api_key: str,
    user_id: int,
    failure_status: str = "failed",
) -> None:
    """生成指定章节正文,缓存到 chapters.final_text / chapter_versions。

    非当前章节由用户单独点选生成时走这里:只生成 LLM-2 正文,不进入审核态,
    也不发布 token。后续流程推进到该章后会复用缓存正文,继续补图表并进入
    人工审核。
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
        # D-EG:template 章节直接渲染,不调 LLM-2
        chapter_type = str(chapter.get("chapter_type") or "normal")
        if chapter_type in _TEMPLATE_TYPES:
            from ..templates import load_pack

            pack: dict[str, Any] | None = None
            template_pack = state.get("template_pack")
            if template_pack:
                try:
                    pack = load_pack(template_pack)
                except FileNotFoundError:
                    pack = None
            final_text = render_template_md(chapter, pack)
            from ..sync import flush_chapter_partial

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
            return

        messages = build_messages(
            chapter=chapter,
            tech_spec_md=state.get("tech_spec_md", ""),
            scoring_md=state.get("scoring_md", ""),
            revision_feedback="",
            retry_count=0,
            previous_text="",
            blackboard_entities=state.get("blackboard_entities"),
            system_override=_SYSTEM_BY_TYPE.get(chapter_type),
            extra_user_directives=_USER_DIRECTIVE_BY_TYPE.get(chapter_type, ""),
        )
        chapter_model = await resolve_chapter_model(project_id, run_id, index, chapter)
        from ..sync import flush_chapter_partial

        async def _on_body_partial(partial_text: str) -> None:
            await flush_chapter_partial(
                run_id,  # type: ignore[arg-type]
                index,
                version_id,
                partial_text,
            )
            await _safe_sync_chapter(
                run_id,
                index,
                processing_started_at=datetime.now(UTC),
            )

        result = await call_llm_stream(
            model=chapter_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            chapter_index=None,
            temperature=0.6,
            on_partial=_on_body_partial,
        )

        from ..postprocess import postprocess_chapter_markdown

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
            status=failure_status,
            processing_started_at=None,
            last_error=f"chapter body generation failed: {e}",
        )
        if failure_status == "failed":
            await publish_event(project_id, "chapter_failed", chapter_index=index, reason=str(e))


async def _render_template_chapter(
    state: WorkflowState,
    current: int,
    chapter: dict[str, Any],
    run_id: int | None,
    project_id: int,
) -> dict[str, Any]:
    """D-EG:``image_only`` / ``table_only`` 章节短路渲染。

    不调 LLM,直接用 ``renderers.render`` 输出固定骨架。仍走完整 DB 落
    state 机:status=generating → save_chapter_version → status=pending,
    保证下游 ``gen_visuals`` / ``human_review`` 拿到一致的输入。
    """
    from ..templates import load_pack

    pack: dict[str, Any] | None = None
    template_pack = state.get("template_pack")
    if template_pack:
        try:
            pack = load_pack(template_pack)
        except FileNotFoundError:
            pack = None

    final_text = render_template_md(chapter, pack)

    await _safe_sync_chapter(
        run_id,
        current,
        status="generating",
        processing_started_at=datetime.now(UTC),
    )
    await publish_event(project_id, "chapter_started", chapter_index=current)

    if _real_run(run_id):
        try:
            from ..sync import flush_chapter_partial, save_chapter_version

            version_id = await save_chapter_version(
                run_id,  # type: ignore[arg-type]
                current,
                final_text,
                feedback_in=None,
            )
            await flush_chapter_partial(
                run_id,  # type: ignore[arg-type]
                current,
                version_id,
                final_text,
            )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_template_render_db_failed",
                run_id=run_id,
                chapter_index=current,
            )

    await _safe_sync_chapter(
        run_id,
        current,
        status="pending",
        processing_started_at=None,
        last_error=None,
    )
    return {"_pending_chapter_text": final_text}


async def run(state: WorkflowState) -> dict[str, Any]:
    current = state["current_index"]
    chapter = state["chapters"][current]
    run_id = state["run_id"]
    project_id = state["project_id"]
    chapter_type = str(chapter.get("chapter_type") or "normal")

    # D-EG:image_only / table_only 章节绕过 LLM-2,直接用 renderer 生成
    # 模板骨架(slot 化的图位 / 表头骨架)。落 DB + 发完成事件后立即返回。
    if chapter_type in _TEMPLATE_TYPES:
        return await _render_template_chapter(state, current, chapter, run_id, project_id)

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

    blackboard_entities = state.get("blackboard_entities")
    blackboard_embeddings = state.get("blackboard_embeddings")
    # Phase 2C:LLM-2 也走 tool calling。条件:全局开关开 + 实体桶非空 +
    # 非 template 章(image_only/table_only 已在前面短路返回,这里不会到)。
    use_chapter_tools = (
        settings.llm_tool_calling_enabled
        and bool(blackboard_entities)
        and any(blackboard_entities.values())  # type: ignore[union-attr]
    )

    # D-EK:为本章查询算一次 query embedding,与 BM25 一起做 RRF 融合
    # D-EO:embedding 模型从 Project 快照读;若快照路径取不到走 settings 兜底
    query_embedding: list[float] | None = None
    embedding_model_name: str | None = None
    if (
        settings.hybrid_retrieval_enabled
        and blackboard_embeddings
        and bool(blackboard_entities)
    ):
        from ..prompts.write_chapter_prompt import _build_chapter_query

        try:
            embedding_model_name = (await resolve_models(project_id)).embedding_model
        except Exception:
            embedding_model_name = None
        try:
            chapter_query = _build_chapter_query(chapter)
            if chapter_query.strip():
                query_embedding = await embed_one(
                    chapter_query,
                    api_key=api_key,
                    model=embedding_model_name,
                    user_id=user_id,
                    project_id=project_id,
                )
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "write_chapter_query_embed_failed",
                run_id=run_id,
                index=current,
            )
            query_embedding = None

    # D-EL:首轮召回写入 references_collector;tool 调用阶段由 handler 追加
    references_collector: list[dict[str, Any]] = []

    messages = build_messages(
        chapter=chapter,
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        revision_feedback=revision_feedback,
        retry_count=retry_count,
        previous_text=previous_text,  # ⭐ R-18
        blackboard_entities=blackboard_entities,
        blackboard_embeddings=blackboard_embeddings,
        query_embedding=query_embedding,
        # D-EG:按 chapter_type 选 system 与 user 末尾指令;normal/未知用默认
        system_override=_SYSTEM_BY_TYPE.get(chapter_type),
        extra_user_directives=_USER_DIRECTIVE_BY_TYPE.get(chapter_type, ""),
        # Phase 2C:tool calling 模式下,user prompt 末尾追加 search_blackboard
        # 工具说明
        tool_calling_enabled=use_chapter_tools,
        references_out=references_collector,
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

    try:
        if use_chapter_tools:
            # Phase 2C:LLM-2 + tool calling 流式
            # BM25 召回作为首轮上下文,LLM 起步就有材料;过程中可主动调
            # search_blackboard 取更多原文。on_partial 在最终答案产出后一次性
            # 触发(典型 1-2 轮工具调用约 60-90s,期间前端没有 token 流入,
            # 算可接受的 UX 退化)。

            async def _embed_for_tool(text: str) -> list[float]:
                # D-EK:tool 调用时的 query 同样走混合召回;失败回退 None,handler 内部转纯 BM25
                # D-EO:复用同一份快照模型名
                return await embed_one(
                    text,
                    api_key=api_key,
                    model=embedding_model_name,
                    user_id=user_id,
                    project_id=project_id,
                )

            tool_handler = make_blackboard_tool_handler(
                blackboard_entities,
                embeddings=blackboard_embeddings,
                query_embedder=_embed_for_tool if blackboard_embeddings else None,
                collector=references_collector,
            )
            result = await call_llm_stream_with_tools(
                model=chapter_model,
                messages=messages,
                api_key=api_key,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                chapter_index=current,
                tools=[SEARCH_BLACKBOARD_TOOL],
                tool_handler=tool_handler,
                max_tool_rounds=settings.llm_chapter_tool_max_rounds,
                temperature=0.6,
                on_partial=_on_partial,
            )
        else:
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

    # D-EL:把 LLM 看过的参考资料落 chapter.references。按 content 去重,
    # 优先保留 bm25+vec / tool 命中(信息更完整)。
    final_refs = _dedupe_references(references_collector)
    if _real_run(run_id) and final_refs:
        await _safe_sync_chapter(
            run_id,
            current,
            references=final_refs,
        )

    return {"_pending_chapter_text": final_text}


def _dedupe_references(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 content 去重,合并 retrieval_method(同条目两路命中标 bm25+vec)。

    保持首次出现顺序;tool 标记优先级最高(LLM 主动查的更说明问题)。
    """
    if not items:
        return []
    by_content: dict[str, dict[str, Any]] = {}
    for raw in items:
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        key = content.strip()
        existing = by_content.get(key)
        if existing is None:
            by_content[key] = dict(raw)
            continue
        prev_method = existing.get("retrieval_method") or ""
        new_method = raw.get("retrieval_method") or ""
        # tool 标记单独保留,其它两路合并成 bm25+vec
        if "tool" in (prev_method, new_method):
            existing["retrieval_method"] = "tool"
        else:
            methods = set(prev_method.split("+")) | set(new_method.split("+"))
            methods.discard("")
            existing["retrieval_method"] = "+".join(sorted(methods))
    return list(by_content.values())
