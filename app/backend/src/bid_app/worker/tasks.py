"""arq workflow tasks(§10.5,D-Z / D-AY / D-AB / D-AC / D-AT / D-AU /
D-AW / D-AZ / D-BA / D-BK / D-BS / D-BT)。

四类任务,**全部 max_tries=1**(D-Z / D-AY):
  · ``start_workflow_task`` — /start 端点,新启动
  · ``resume_review_task``  — /review / /confirm-outline 端点,从 interrupt 恢复
  · ``retry_failed_chapter_task`` — /chapters/{idx}/retry 端点,失败章节续跑
  · ``generate_docx_task``  — /docx POST 端点

⚠️ arq 0.26.x 的 ``arq.worker.func`` 不是装饰器工厂(``func(coroutine, *,
max_tries=...)``,coroutine 是必填位置参数);spec §17.2 的 ``@func(max_tries=1)``
写法在该版本不可用——会抛 ``TypeError: func() missing 1 required positional
argument: 'coroutine'``。
正确做法:任务保持 plain async function,在 ``worker/settings.py`` 的
``WorkerSettings.functions`` 列表里用 ``func(start_workflow_task, max_tries=1)``
逐个包装(返 ``Function`` 对象)。
"""

from __future__ import annotations

import contextlib
import traceback
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
import structlog
from langgraph.types import Command
from sqlalchemy import CursorResult

from ..config import settings
from ..core.error_log import append_error
from ..db import session_factory
from ..models import ReviewEvent
from ..services.concurrency import (
    SlotLost,
    ensure_project_slot,
    project_heartbeat,
    release_project_slot,
    try_acquire_project_slot,
    wake_queued_projects,
)
from ..services.document_extractor import extract_file, extract_for_project
from ..services.docx_export import export_chapter_docx, export_docx
from ..services.llm import ChapterGenerationFailed
from ..workflow.graph import build_graph
from ..workflow.state import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    WorkflowSchemaMismatch,
    WorkflowState,
    ensure_current_state,
)


class _StaleJob(Exception):
    """阶段切换时发现 cleanup 已抢标 failed,放弃任务(D-BX)。"""


log = structlog.get_logger()


# ----- helpers ----------------------------------------------------------


async def _project_dir(project_id: int) -> Path:
    """从 DB 取项目目录,用于错误日志写入。"""
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"),
            {"p": project_id},
        )
        return Path(row.scalar_one())


async def _set_project_status(project_id: int, status: str) -> None:
    async with session_factory() as s:
        await s.execute(
            sa.text("UPDATE projects SET status=:s WHERE id=:p"),
            {"s": status, "p": project_id},
        )
        await s.commit()


async def _fail_project_and_run(project_id: int, run_id: int, error: str) -> None:
    """⭐ D-BA:task 顶层 generic exception 时,Project 与 Run 一起标 failed。

    Run.error 字段最长 4000,截断保护;``finished_at=NOW()``。
    幂等:Run 已经是 done/failed/aborted 时不动(WHERE status='running')。
    """
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET status='failed' WHERE id=:p"),
                {"p": project_id},
            )
            await s.execute(
                sa.text(
                    "UPDATE runs SET status='failed', "
                    "finished_at=NOW(), error=:e "
                    "WHERE id=:r AND status='running'"
                ),
                {"r": run_id, "e": error[:4000]},
            )
            await s.commit()
    except Exception:
        log.exception(
            "fail_project_and_run_failed",
            project_id=project_id,
            run_id=run_id,
        )


async def _ensure_or_reacquire(
    project_id: int,
    slot_token: str,
    run_id: int,
    action: str,
) -> str | None:
    """task 入口的 lease 校验。返回字符串 token 或 None。

    1. token 仍匹配 → 返回原 token
    2. token 失效但有空名额 → 重新 acquire,返回新 token
    3. token 失效且没空名额 → /start 切 queued、其它 action 不动,返回 None
    """
    if await ensure_project_slot(project_id, slot_token):
        return slot_token

    log.warning(
        "slot_token_lost",
        project_id=project_id,
        action=action,
        run_id=run_id,
        hint="reservation TTL expired or reconciled",
    )

    result = await try_acquire_project_slot(project_id)
    if result.acquired:
        log.info("slot_reacquired", project_id=project_id, action=action)
        assert result.token is not None
        return result.token

    if result.reason == "already_active":
        log.error(
            "ensure_inconsistent_already_active",
            project_id=project_id,
            action=action,
            hint="someone else acquired same project_id?",
        )
        return None

    async with session_factory() as s:
        if action == "start":
            await s.execute(
                sa.text("UPDATE projects SET status='queued' WHERE id=:p"),
                {"p": project_id},
            )
        await s.commit()
    log.warning("slot_lost_no_capacity", project_id=project_id, action=action)
    return None


async def build_initial_state(project_id: int, run_id: int) -> WorkflowState:
    """从 DB 读项目 + 文档,构造 WorkflowState 给 graph.astream 起点用。"""
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT pages_per_chapter, max_retry_per_chapter FROM projects WHERE id=:p"),
            {"p": project_id},
        )
        prj = row.one()

    docs = await extract_for_project(project_id)
    return {
        "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
        "project_id": project_id,
        "run_id": run_id,
        "tech_spec_md": docs.get("tech_spec_md", ""),
        "scoring_md": docs.get("scoring_md", ""),
        "template_md": docs.get("template_md", ""),
        "pages_per_chapter": int(prj.pages_per_chapter or 3),
        "max_retry_per_chapter": int(prj.max_retry_per_chapter or 3),
        "chapters": [],
        "current_index": 0,
        "retry_count": 0,
        "finalized_chapters": [],
        "revision_feedback": "",
        # Phase 1A:实体桶字段在 schema v3 起算 state 必填,初始为 None,
        # 由 categorize_blackboard 节点填充。
        "blackboard_entities": None,
    }


async def build_chapter_body_state(project_id: int, run_id: int) -> WorkflowState:
    """构造单章正文预生成所需的最小 WorkflowState。"""
    async with session_factory() as s:
        project_row = await s.execute(
            sa.text(
                "SELECT pages_per_chapter, max_retry_per_chapter, "
                "blackboard_entities "
                "FROM projects WHERE id=:p"
            ),
            {"p": project_id},
        )
        prj = project_row.one()

        chapter_rows = (
            (
                await s.execute(
                    sa.text(
                        "SELECT index, title, summary, key_points, target_pages, model_snapshot "
                        "FROM chapters WHERE run_id=:r ORDER BY index ASC"
                    ),
                    {"r": run_id},
                )
            )
            .mappings()
            .all()
        )

    docs = await extract_for_project(project_id)
    chapters = [
        {
            "title": row["title"],
            "summary": row["summary"],
            "key_points": row["key_points"] or [],
            "target_pages": int(row["target_pages"] or 3),
            "chapter_model": row["model_snapshot"],
        }
        for row in chapter_rows
    ]
    return {
        "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
        "project_id": project_id,
        "run_id": run_id,
        "tech_spec_md": docs.get("tech_spec_md", ""),
        "scoring_md": docs.get("scoring_md", ""),
        "template_md": docs.get("template_md", ""),
        "pages_per_chapter": int(prj.pages_per_chapter or 3),
        "max_retry_per_chapter": int(prj.max_retry_per_chapter or 3),
        "chapters": chapters,
        "current_index": 0,
        "retry_count": 0,
        "finalized_chapters": [],
        "revision_feedback": "",
        # ⭐ 预生成路径与主 workflow 章节生成保持一致 — 都把结构化实体桶
        # 注入 state,write_chapter 才会按 Phase 2A BM25 检索增强 prompt;
        # 否则用户「预生成正文」拿到的章节质量与主流程不一致。
        "blackboard_entities": prj.blackboard_entities,
    }


async def _prepare_chapter_body_generation(run_id: int, chapter_index: int) -> int | None:
    """确认章节仍占用 generation slot,并废弃旧的未审正文版本。"""
    from ..workflow.sync import _mark_chapter_versions_abandoned_in_session

    async with session_factory() as s:
        row = (
            (
                await s.execute(
                    sa.text(
                        "SELECT id, status FROM chapters WHERE run_id=:r AND index=:i FOR UPDATE"
                    ),
                    {"r": run_id, "i": chapter_index},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["status"] != "generating":
            log.info(
                "chapter_body_generation_no_longer_needed",
                run_id=run_id,
                chapter_index=chapter_index,
                status=row["status"],
            )
            await s.commit()
            return None

        chapter_id = int(row["id"])
        await _mark_chapter_versions_abandoned_in_session(s, chapter_id)
        await s.execute(
            sa.text("UPDATE chapters SET final_text=NULL, last_error=NULL WHERE id=:c"),
            {"c": chapter_id},
        )
        await s.commit()

    # final_text 即将被新生成覆盖 → 旧 chapter_{id}.docx 缓存作废
    from ..services.docx_invalidation import invalidate_chapter_docx

    await invalidate_chapter_docx(chapter_id)
    return chapter_id


async def _slot_lost_compensation(
    project_id: int,
    run_id: int,
    *,
    current_chapter_id: int | None,
    action: str,
    decision: str | None = None,
    review_event_id: int | None = None,
) -> None:
    """⭐ D-AW + D-AZ + D-BI + D-BM + D-BT:SlotLost 路径统一补偿。"""
    rolled_back = 0
    try:
        async with session_factory() as s:
            if current_chapter_id is not None:
                if decision in ("approve", "skip"):
                    r1 = await s.execute(
                        sa.text(
                            "UPDATE chapters SET status='awaiting_review', "
                            "processing_started_at=NULL "
                            "WHERE id=:c AND status='reviewing' "
                            "RETURNING id"
                        ),
                        {"c": current_chapter_id},
                    )
                    chapter_rolled_back = r1.fetchall()
                    rolled_back += len(chapter_rolled_back)
                    if chapter_rolled_back and review_event_id is not None:
                        # ⭐ D-BI + D-BT:精确按 review_event_id 标 aborted
                        await s.execute(
                            sa.text(
                                "UPDATE review_events SET aborted=true "
                                "WHERE id=:rev_id AND aborted=false"
                            ),
                            {"rev_id": review_event_id},
                        )
                    elif chapter_rolled_back:
                        log.warning(
                            "slot_lost_review_event_id_missing",
                            chapter_id=current_chapter_id,
                        )
                        await s.execute(
                            sa.text(
                                "UPDATE review_events SET aborted=true "
                                "WHERE id = ("
                                "  SELECT id FROM review_events "
                                "  WHERE chapter_id=:c AND aborted=false "
                                "  ORDER BY created_at DESC LIMIT 1"
                                ")"
                            ),
                            {"c": current_chapter_id},
                        )
                else:
                    r1 = await s.execute(
                        sa.text(
                            "UPDATE chapters SET status='failed', "
                            "processing_started_at=NULL, "
                            "last_error=COALESCE(NULLIF(last_error,''),'') || "
                            "  CASE WHEN COALESCE(last_error,'')='' "
                            "       THEN '' ELSE ' | ' END || "
                            "  'slot lost during ' || :a "
                            "WHERE id=:c "
                            "AND status IN ('reviewing','retrying','pending','generating') "
                            "RETURNING id"
                        ),
                        {"c": current_chapter_id, "a": action},
                    )
                    rolled_back += len(r1.fetchall())
            r2 = await s.execute(
                sa.text(
                    "UPDATE chapters SET status='failed', "
                    "processing_started_at=NULL, "
                    "last_error='slot lost during chapter generation' "
                    "WHERE run_id=:r AND status='generating' RETURNING id"
                ),
                {"r": run_id},
            )
            rolled_back += len(r2.fetchall())
            await s.commit()
    except Exception:
        log.exception(
            "slot_lost_chapter_rollback_failed",
            project_id=project_id,
            run_id=run_id,
        )

    if rolled_back > 0:
        try:
            await _set_project_status(project_id, "awaiting_review")
        except Exception:
            log.exception("slot_lost_project_status_failed", project_id=project_id)
    else:
        log.warning(
            "slot_lost_no_chapter_rolled_back",
            project_id=project_id,
            action=action,
            hint="left for reconcile to mark failed",
        )


# ----- workflow tasks ---------------------------------------------------


async def _abort_project_schema_v1(project_id: int, run_id: int, action: str) -> None:
    """⭐ PR-M7-1:checkpoint schema 不匹配,标 project 'aborted_schema_v1'。

    与 ``_fail_project_and_run`` 区分:这条路径不算 task crash,只是 v2 上线
    遗留的 v1 项目无法 resume。UI 看到 ``aborted_schema_v1`` 时提示用户重建。
    """
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE projects SET status='aborted_schema_v1' "
                    "WHERE id=:p AND status NOT IN ('done', 'failed')"
                ),
                {"p": project_id},
            )
            await s.execute(
                sa.text(
                    "UPDATE runs SET status='aborted', "
                    "finished_at=NOW(), error='workflow schema v1 → v2' "
                    "WHERE id=:r AND status='running'"
                ),
                {"r": run_id},
            )
            await s.commit()
    except Exception:
        log.exception(
            "schema_v1_abort_db_update_failed",
            project_id=project_id,
            run_id=run_id,
            action=action,
        )


async def start_workflow_task(
    ctx: dict[str, Any],
    *,
    project_id: int,
    run_id: int,
    thread_id: str,
    slot_token: str,
) -> None:
    """全新启动。/start 已 try_acquire 拿到 slot_token,task 入口校验 token。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "start")
    if token is None:
        return

    try:
        async with project_heartbeat(project_id, token) as lost_event:
            await _set_project_status(project_id, "running")
            initial = await build_initial_state(project_id, run_id)
            async for state in graph.astream(initial, config, stream_mode="values"):
                # ⭐ PR-M7-1:每个 state snapshot 校验 schema_version
                ensure_current_state(state)
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during start, project_id={project_id}")
    except SlotLost:
        await _slot_lost_compensation(
            project_id,
            run_id,
            current_chapter_id=None,
            action="start",
        )
        log.warning("start_workflow_task_aborted_slot_lost", project_id=project_id)
    except WorkflowSchemaMismatch as e:
        # ⭐ PR-M7-1:v1 checkpoint 在 v2 graph 无法 resume;标 aborted_schema_v1
        await _abort_project_schema_v1(project_id, run_id, action="start")
        log.warning(
            "start_workflow_task_schema_mismatch",
            project_id=project_id,
            found=e.found,
            current=e.current,
        )
    except ChapterGenerationFailed as e:
        await _set_project_status(project_id, "awaiting_review")
        log.info(
            "start_workflow_task_chapter_failed",
            project_id=project_id,
            chapter_index=e.chapter_index,
        )
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(
                pdir,
                "start_workflow_task crashed",
                run_id=run_id,
                thread_id=thread_id,
                traceback=tb,
            )
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _fail_project_and_run(project_id, run_id, "start_workflow_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


async def resume_review_task(
    ctx: dict[str, Any],
    *,
    project_id: int,
    run_id: int,
    thread_id: str,
    resume_payload: dict[str, Any],
    slot_token: str,
    reviewer_id: int | None = None,
    chapter_id: int | None = None,
) -> None:
    """从 interrupt 恢复(/review、/confirm-outline)。

    worker 入口写 ``ReviewEvent`` 保证事件与执行同生同灭(D-AC)。
    """
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "resume")
    if token is None:
        return

    review_decision: str | None = None
    review_event_id: int | None = None
    try:
        # ⭐ D-AC + D-AZ:worker 入口写 ReviewEvent + 按 decision 切章节状态
        if reviewer_id is not None and chapter_id is not None:
            kind = (resume_payload or {}).get("kind")
            if kind == "chapter_review":
                review_decision = resume_payload.get("decision")
                async with session_factory() as s:
                    rev = ReviewEvent(
                        chapter_id=chapter_id,
                        reviewer_id=reviewer_id,
                        decision=review_decision or "approve",
                        feedback_text=resume_payload.get("feedback") or None,
                    )
                    s.add(rev)
                    await s.flush()  # ⭐ D-BT:flush 拿 PK
                    review_event_id = rev.id
                    await s.commit()

        async with project_heartbeat(project_id, token) as lost_event:
            async for state in graph.astream(
                Command(resume=resume_payload),
                config,
                stream_mode="values",
            ):
                # ⭐ PR-M7-1:resume 路径上也要校验 v1 → v2 不兼容 checkpoint
                ensure_current_state(state)
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during resume, project_id={project_id}")
    except SlotLost:
        await _slot_lost_compensation(
            project_id,
            run_id,
            current_chapter_id=chapter_id,
            action="resume",
            decision=review_decision,
            review_event_id=review_event_id,
        )
        log.warning("resume_review_task_aborted_slot_lost", project_id=project_id)
    except WorkflowSchemaMismatch as e:
        await _abort_project_schema_v1(project_id, run_id, action="resume")
        log.warning(
            "resume_review_task_schema_mismatch",
            project_id=project_id,
            found=e.found,
            current=e.current,
        )
    except ChapterGenerationFailed as e:
        await _set_project_status(project_id, "awaiting_review")
        log.info(
            "resume_review_task_chapter_failed",
            project_id=project_id,
            chapter_index=e.chapter_index,
        )
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(
                pdir,
                "resume_review_task crashed",
                run_id=run_id,
                payload=resume_payload,
                traceback=tb,
            )
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _fail_project_and_run(project_id, run_id, "resume_review_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


async def generate_chapter_body_task(
    ctx: dict[str, Any],
    *,
    project_id: int,
    run_id: int,
    chapter_index: int,
    reviewer_id: int | None = None,
    chapter_id: int | None = None,
) -> None:
    """用户点选非当前章节时,只生成 LLM-2 正文缓存。"""
    del ctx
    try:
        prepared_chapter_id = await _prepare_chapter_body_generation(run_id, chapter_index)
        if prepared_chapter_id is None:
            return

        if reviewer_id is not None:
            async with session_factory() as s:
                s.add(
                    ReviewEvent(
                        chapter_id=chapter_id or prepared_chapter_id,
                        reviewer_id=reviewer_id,
                        decision="retry_body_generation",
                    )
                )
                await s.commit()

        from ..services.concurrency import chapter_heartbeat
        from ..workflow.nodes.write_chapter import _prefetch_chapter_body
        from ..workflow.resolve import resolve_api_key, resolve_user_id

        state = await build_chapter_body_state(project_id, run_id)
        if chapter_index >= len(state.get("chapters") or []):
            raise RuntimeError(f"chapter index out of range: {chapter_index}")

        api_key = await resolve_api_key(project_id, run_id=run_id)
        user_id = await resolve_user_id(project_id)
        # ⭐ Redis chapter heartbeat:本任务不占 project slot,而 cleanup_stale_chapters
        # 默认 3 分钟把 generating 标 failed —— LLM 首 token 慢就误杀。心跳 key
        # 让 cleanup 在 SQL 维度排除这个 chapter id。
        async with chapter_heartbeat(prepared_chapter_id):
            await _prefetch_chapter_body(
                state,
                chapter_index,
                api_key=api_key,
                user_id=user_id,
                failure_status="failed",
            )
    except Exception as e:
        log.exception(
            "generate_chapter_body_task_failed",
            project_id=project_id,
            run_id=run_id,
            chapter_index=chapter_index,
        )
        try:
            from ..workflow.sync import publish_event, sync_chapter_to_db

            await sync_chapter_to_db(
                run_id,
                chapter_index,
                status="failed",
                processing_started_at=None,
                last_error=str(e)[:4000],
            )
            await publish_event(
                project_id,
                "chapter_failed",
                chapter_index=chapter_index,
                reason=str(e),
            )
        except Exception:
            log.exception(
                "generate_chapter_body_task_compensation_failed",
                project_id=project_id,
                run_id=run_id,
                chapter_index=chapter_index,
            )
        raise


async def retry_failed_chapter_task(
    ctx: dict[str, Any],
    *,
    project_id: int,
    run_id: int,
    thread_id: str,
    chapter_index: int,
    reviewer_id: int,
    chapter_id: int,
    slot_token: str,
) -> None:
    """API 端点已 try_acquire 成功才入队;DB 重置 + 续跑;
    worker 入口写 ReviewEvent。"""
    arq_pool = ctx["arq_pool"]
    saver = ctx["checkpointer"]
    graph = build_graph(saver)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    token = await _ensure_or_reacquire(project_id, slot_token, run_id, "retry")
    if token is None:
        return

    try:
        # ⭐ D-AC + D-AD + FR-4.7:写 ReviewEvent + 保持生成槽占用 +
        # 章节所有非 abandoned 版本标 abandoned=true(走 sync helper 单一信源)
        from ..workflow.sync import _mark_chapter_versions_abandoned_in_session

        async with session_factory() as s:
            s.add(
                ReviewEvent(
                    chapter_id=chapter_id,
                    reviewer_id=reviewer_id,
                    decision="retry_failed",
                )
            )
            await _mark_chapter_versions_abandoned_in_session(s, chapter_id)
            # ⭐ D-BS:重试接管后继续占用 generating slot,直到 write_chapter
            # / human_review 闭合状态。
            await s.execute(
                sa.text(
                    "UPDATE chapters SET status='generating', retry_count=0, "
                    "final_text=NULL, last_error=NULL, processing_started_at=NOW() "
                    "WHERE id=:c AND status='retrying'"
                ),
                {"c": chapter_id},
            )
            await s.commit()

        # 重试会重新生成 final_text → 旧 chapter_{id}.docx 缓存作废
        from ..services.docx_invalidation import invalidate_chapter_docx

        await invalidate_chapter_docx(chapter_id)

        async with project_heartbeat(project_id, token) as lost_event:
            await graph.aupdate_state(config, {"retry_count": 0})
            async for state in graph.astream(None, config, stream_mode="values"):
                # ⭐ PR-M7-1:retry 也走 checkpoint resume,同样校验 schema
                ensure_current_state(state)
                if lost_event.is_set() or not await ensure_project_slot(project_id, token):
                    raise SlotLost(f"slot lost during retry, project_id={project_id}")
    except SlotLost:
        await _slot_lost_compensation(
            project_id,
            run_id,
            current_chapter_id=chapter_id,
            action="retry",
        )
        log.warning(
            "retry_failed_chapter_task_aborted_slot_lost",
            project_id=project_id,
        )
    except WorkflowSchemaMismatch as e:
        await _abort_project_schema_v1(project_id, run_id, action="retry")
        log.warning(
            "retry_failed_chapter_task_schema_mismatch",
            project_id=project_id,
            found=e.found,
            current=e.current,
        )
    except ChapterGenerationFailed as e:
        await _set_project_status(project_id, "awaiting_review")
        log.info(
            "retry_failed_chapter_task_chapter_failed",
            project_id=project_id,
            chapter_index=e.chapter_index,
        )
    except Exception:
        tb = traceback.format_exc()
        try:
            pdir = await _project_dir(project_id)
            await append_error(
                pdir,
                "retry_failed_chapter_task crashed",
                run_id=run_id,
                chapter_index=chapter_index,
                traceback=tb,
            )
        except Exception:
            log.exception("error_log_write_failed", project_id=project_id)
        await _fail_project_and_run(project_id, run_id, "retry_failed_chapter_task crashed")
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


# ----- generate_docx_task (M3-2 真实现) ---------------------------------

# ⭐ D-BX:阶段切换 WHERE 前置守护;每个目标状态的合法前驱
_DOCX_STAGE_ALLOWED_FROM: dict[str, tuple[str, ...]] = {
    "pandoc": ("rendering_mermaid",),
}


async def _fail_docx_job_from_pending(docx_job_id: int, error: str) -> None:
    """前置失败兜底:在 pending 阶段(还没切到 rendering_mermaid)发生异常
    时,立即把 docx_jobs 行打 failed,避免要等 30 分钟 cleanup cron 才标。

    用 ``status='pending'`` WHERE 守护防覆盖:已被并发(cleanup / 其它路径)
    标终态的行不动。"""
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', error=:e, "
                    "finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='pending'"
                ),
                {"i": docx_job_id, "e": error[:4000]},
            )
            await s.commit()
    except Exception:
        log.exception(
            "docx_pre_render_failed_mark_failed_failed",
            docx_job_id=docx_job_id,
        )


async def _commit_docx_done(docx_job_id: int, final_path: Path) -> dict[str, str | None]:
    """⭐ D-CL + D-CQ + D-CV:finalizing → done 提交逻辑。

    DB 是 source of truth;文件残留是 best-effort cleanup 失败的回退,
    API 层(D-CJ)以 latest job 状态决定是否放行下载。invalidated 分支
    尝试 unlink final_path 是 best-effort,失败仅 log。
    """
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE docx_jobs SET status='done', output_path=:p, "
                "finished_at=NOW(), updated_at=NOW() "
                "WHERE id=:i AND status='finalizing'"
            ),
            {"i": docx_job_id, "p": str(final_path)},
        )
        await s.commit()
        if cast(CursorResult[Any], result).rowcount == 0:
            cur = (
                await s.execute(
                    sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
                    {"i": docx_job_id},
                )
            ).scalar_one_or_none()
            if cur == "done":
                log.info(
                    "docx_done_already_repaired",
                    docx_job_id=docx_job_id,
                    hint="D-BY/D-CD 抢先修复,本 task 不再重复写",
                )
            elif cur == "invalidated":
                log.info(
                    "docx_invalidated_during_commit_unlink",
                    docx_job_id=docx_job_id,
                )
                try:
                    final_path.unlink(missing_ok=True)
                except Exception:
                    log.exception("docx_invalidated_unlink_failed", path=str(final_path))
                return {"status": "invalidated", "output_path": None}
            else:
                log.warning(
                    "docx_done_status_diverged",
                    docx_job_id=docx_job_id,
                    current_status=cur,
                    hint="finalizing 期间被改走;文件已 rename 但 DB 不是 done",
                )
                return {"status": "stale", "output_path": str(final_path)}

    return {"status": "done", "output_path": str(final_path)}


async def generate_docx_task(  # max_tries=1 在 worker/settings.py functions 列表里 wrap(D-AY)
    ctx: dict[str, Any],
    *,
    project_id: int,
    docx_job_id: int,
) -> dict[str, Any]:
    """串行锁在 export_docx 内部实现(D-H)。

    PR-M6-2:按 ``DocxJob.scope`` 分发到 project / chapter 两条路径。
    chapter scope 走单章 markdown (``Chapter.final_text``) + 章节专属 final 路径
    (``chapter_{id}.docx``);project scope 维持原全本流程不动。
    """
    # 0. ⭐ D-AK:校验 DocxJob row 存在 + 读 scope/chapter_id 决定分支
    async with session_factory() as s:
        existing_row = (
            (
                await s.execute(
                    sa.text("SELECT status, scope, chapter_id FROM docx_jobs WHERE id=:i"),
                    {"i": docx_job_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_row is None:
            log.error(
                "docx_job_row_missing",
                docx_job_id=docx_job_id,
                project_id=project_id,
                hint="API 端 commit 可能失败;arq 仍把 task 入了队",
            )
            return {"error": "docx_job row not found"}
        existing = existing_row["status"]
        scope = existing_row["scope"] or "project"
        chapter_id = existing_row["chapter_id"]
        if existing in ("done", "failed"):
            log.warning(
                "docx_job_already_finished",
                docx_job_id=docx_job_id,
                status=existing,
            )
            return {"status": existing}
        # ⭐ D-CM:invalidated 守护 — assemble 已作废
        if existing == "invalidated":
            log.info(
                "docx_job_already_invalidated",
                docx_job_id=docx_job_id,
                hint="markdown 重生成,本任务的产物不再有效",
            )
            return {"status": "invalidated"}

    if scope == "chapter":
        if chapter_id is None:
            raise RuntimeError(f"docx_job {docx_job_id} scope=chapter but chapter_id NULL")
        return await _run_chapter_docx_pipeline(
            project_id=project_id,
            docx_job_id=docx_job_id,
            chapter_id=int(chapter_id),
        )

    return await _run_project_docx_pipeline(project_id=project_id, docx_job_id=docx_job_id)


async def _run_project_docx_pipeline(
    *, project_id: int, docx_job_id: int
) -> dict[str, Any]:  # 1. 取项目 markdown + dir + name
    # 1a. 前置 IO / DB 检查;任一失败立即把 job 标 failed 后再 raise,
    # 否则用户轮询会看到一直 processing 直到 30 分钟 stale cleanup。
    try:
        async with session_factory() as s:
            prj_row = await s.execute(
                sa.text("SELECT name, dir_path FROM projects WHERE id=:p"),
                {"p": project_id},
            )
            prj_one = prj_row.one_or_none()
            if prj_one is None:
                raise RuntimeError(f"project {project_id} not found")
            project_name, project_dir_str = prj_one
            project_dir = Path(project_dir_str)

            run_row = await s.execute(
                sa.text(
                    "SELECT id FROM runs WHERE project_id=:p AND status='done' "
                    "ORDER BY finished_at DESC LIMIT 1"
                ),
                {"p": project_id},
            )
            run_id = run_row.scalar_one_or_none()
            if run_id is None:
                raise RuntimeError(f"project {project_id} has no completed run")

            md_path = project_dir / "proposal.md"
            if not md_path.exists():
                raise RuntimeError(f"proposal.md missing at {md_path}")
            markdown = md_path.read_text(encoding="utf-8")
    except Exception as e:
        await _fail_docx_job_from_pending(docx_job_id, f"precheck: {e!r}")
        raise

    # 2. ⭐ D-BX:进 rendering_mermaid 加 WHERE status='pending' 前置
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE docx_jobs SET status='rendering_mermaid', "
                "updated_at=NOW() WHERE id=:i AND status='pending'"
            ),
            {"i": docx_job_id},
        )
        await s.commit()
        if cast(CursorResult[Any], result).rowcount == 0:
            log.warning(
                "docx_stage_blocked_at_rendering",
                docx_job_id=docx_job_id,
                hint="cleanup 已把 job 标 failed;不再启动 mermaid 渲染",
            )
            return {"status": "stale", "output_path": None}

    # ⭐ D-CU:进 rendering 阶段后,**立即强制 unlink 旧 final_path**
    final_path = project_dir / "proposal.docx"
    try:
        final_path.unlink(missing_ok=True)
    except OSError as e:
        log.exception(
            "docx_pre_render_unlink_failed",
            docx_job_id=docx_job_id,
            path=str(final_path),
        )
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error=:e, finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='rendering_mermaid'"
                ),
                {
                    "i": docx_job_id,
                    "e": f"failed to clear stale final: {e!r}"[:4000],
                },
            )
            await s.commit()
        raise

    # ⭐ D-BD + D-BH + D-BX:on_stage 切换 WHERE status 前置 + rowcount 守护
    async def _update_stage(stage: str) -> None:
        prev_states = _DOCX_STAGE_ALLOWED_FROM.get(stage)
        if not prev_states:
            raise ValueError(f"unknown stage: {stage}")
        in_clause = ",".join(f"'{s}'" for s in prev_states)
        async with session_factory() as s:
            r = await s.execute(
                sa.text(
                    f"UPDATE docx_jobs SET status=:s, updated_at=NOW() "
                    f"WHERE id=:i AND status IN ({in_clause})"
                ),
                {"s": stage, "i": docx_job_id},
            )
            await s.commit()
            if cast(CursorResult[Any], r).rowcount == 0:
                log.warning(
                    "docx_stage_blocked",
                    docx_job_id=docx_job_id,
                    stage=stage,
                )
                raise _StaleJob(stage)

    # 3. 真正执行(锁在 export_docx 内,产物写入 tmp)
    try:
        tmp_path = await export_docx(
            markdown=markdown,
            project_dir=project_dir,
            project_name=project_name,
            reference_doc=Path(settings.templates_dir) / "reference.docx",
            redis_url=settings.redis_url,
            on_stage=_update_stage,
            job_id=docx_job_id,  # ⭐ D-BN:tmp 文件名后缀
        )
    except _StaleJob as se:
        # ⭐ D-BX:cleanup 抢标 failed,清半成品 tmp 后退出
        log.info(
            "docx_task_stale_exit",
            docx_job_id=docx_job_id,
            stage=str(se),
        )
        try:
            (project_dir / f"proposal.{docx_job_id}.tmp.docx").unlink(missing_ok=True)
        except Exception:
            log.exception("docx_tmp_unlink_failed", docx_job_id=docx_job_id)
        return {"status": "stale", "output_path": None}
    except Exception as e:
        # ⭐ D-BH + D-BQ:WHERE 覆盖所有 in-flight 防覆盖 cleanup 抢标的行
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', error=:e, "
                    "finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status IN "
                    "('pending','rendering_mermaid','pandoc','finalizing')"
                ),
                {"i": docx_job_id, "e": str(e)[:4000]},
            )
            await s.commit()
        raise

    # ⭐ D-BQ:抢占 finalizing(WHERE 防覆盖 cleanup);先 finalizing → rename → done
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE docx_jobs SET status='finalizing', updated_at=NOW() "
                "WHERE id=:i AND status IN "
                "('pending','rendering_mermaid','pandoc')"
            ),
            {"i": docx_job_id},
        )
        await s.commit()
        if cast(CursorResult[Any], result).rowcount == 0:
            log.warning(
                "docx_finalize_blocked",
                docx_job_id=docx_job_id,
                hint="cleanup 已把 job 标 failed;丢弃 tmp 产出",
            )
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                log.exception("docx_tmp_unlink_failed", tmp=str(tmp_path))
            return {"status": "stale", "output_path": None}

    # ⭐ D-CQ:rename 前再查一次 status 防御 — assemble 可能在 finalizing 抢占 /
    # rename 之间把 status 改 invalidated
    async with session_factory() as s:
        cur = (
            await s.execute(
                sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
                {"i": docx_job_id},
            )
        ).scalar_one_or_none()
    if cur == "invalidated":
        log.info(
            "docx_skip_rename_invalidated_during_finalize",
            docx_job_id=docx_job_id,
        )
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            log.exception("docx_tmp_unlink_failed", tmp=str(tmp_path))
        return {"status": "invalidated", "output_path": None}

    # ⭐ D-BN + D-BQ:finalizing 之后才 atomic rename
    try:
        tmp_path.rename(final_path)
    except Exception:
        log.exception(
            "docx_atomic_rename_failed",
            tmp=str(tmp_path),
            final=str(final_path),
        )
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error='atomic rename failed', finished_at=NOW(), "
                    "updated_at=NOW(), output_path=NULL "
                    "WHERE id=:i AND status='finalizing'"
                ),
                {"i": docx_job_id},
            )
            await s.commit()
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)
        raise

    # ⭐ D-BQ + D-CE + D-CL:rename 成功才 commit done
    return await _commit_docx_done(docx_job_id, final_path)


# ----- chapter scope (PR-M6-2) -----------------------------------------


async def _run_chapter_docx_pipeline(
    *,
    project_id: int,
    docx_job_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    """单章 DOCX 导出。共享 export pipeline 与全局串行锁;状态机与 project
    scope 完全对齐(pending → rendering_mermaid → pandoc → finalizing → done),
    但 final 路径不固定 ``proposal.docx``,而是 ``chapter_{chapter_id}.docx``;
    invalidation flow 不复用 — 单章导出不依赖整本 assemble。
    """
    # 1. 取章节正文 + 项目目录(前置失败立即落 failed,不卡 pending)
    try:
        async with session_factory() as s:
            chapter_row = (
                (
                    await s.execute(
                        sa.text(
                            "SELECT c.final_text, c.status, c.title, c.index, "
                            "p.id AS project_id, p.dir_path "
                            "FROM chapters c "
                            "JOIN runs r ON r.id = c.run_id "
                            "JOIN projects p ON p.id = r.project_id "
                            "WHERE c.id = :c"
                        ),
                        {"c": chapter_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if chapter_row is None:
                raise RuntimeError(f"chapter {chapter_id} not found")
            if chapter_row["project_id"] != project_id:
                raise RuntimeError(
                    f"chapter {chapter_id} belongs to project "
                    f"{chapter_row['project_id']}, not {project_id}"
                )
            markdown = chapter_row["final_text"]
            project_dir = Path(chapter_row["dir_path"])
    except Exception as e:
        await _fail_docx_job_from_pending(docx_job_id, f"precheck: {e!r}")
        raise

    if not markdown:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error='chapter has no final_text', finished_at=NOW(), "
                    "updated_at=NOW() WHERE id=:i AND status='pending'"
                ),
                {"i": docx_job_id},
            )
            await s.commit()
        raise RuntimeError(f"chapter {chapter_id} has no final_text — generate it first")

    # 2. ⭐ D-BX:进 rendering_mermaid 加 WHERE status='pending' 前置
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE docx_jobs SET status='rendering_mermaid', "
                "updated_at=NOW() WHERE id=:i AND status='pending'"
            ),
            {"i": docx_job_id},
        )
        await s.commit()
        if cast(CursorResult[Any], result).rowcount == 0:
            log.warning(
                "docx_chapter_stage_blocked_at_rendering",
                docx_job_id=docx_job_id,
                hint="cleanup 已把 job 标 failed;不再启动 mermaid 渲染",
            )
            return {"status": "stale", "output_path": None}

    # 3. ⭐ D-CU:清旧 final
    final_path = project_dir / f"chapter_{chapter_id}.docx"
    try:
        final_path.unlink(missing_ok=True)
    except OSError as e:
        log.exception(
            "docx_chapter_pre_render_unlink_failed",
            docx_job_id=docx_job_id,
            path=str(final_path),
        )
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error=:e, finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status='rendering_mermaid'"
                ),
                {
                    "i": docx_job_id,
                    "e": f"failed to clear stale chapter docx: {e!r}"[:4000],
                },
            )
            await s.commit()
        raise

    async def _update_stage(stage: str) -> None:
        prev_states = _DOCX_STAGE_ALLOWED_FROM.get(stage)
        if not prev_states:
            raise ValueError(f"unknown stage: {stage}")
        in_clause = ",".join(f"'{s}'" for s in prev_states)
        async with session_factory() as s:
            r = await s.execute(
                sa.text(
                    f"UPDATE docx_jobs SET status=:s, updated_at=NOW() "
                    f"WHERE id=:i AND status IN ({in_clause})"
                ),
                {"s": stage, "i": docx_job_id},
            )
            await s.commit()
            if cast(CursorResult[Any], r).rowcount == 0:
                log.warning(
                    "docx_chapter_stage_blocked",
                    docx_job_id=docx_job_id,
                    stage=stage,
                )
                raise _StaleJob(stage)

    # 4. pipeline 调用
    try:
        tmp_path = await export_chapter_docx(
            markdown=markdown,
            project_dir=project_dir,
            chapter_id=chapter_id,
            reference_doc=Path(settings.templates_dir) / "reference.docx",
            redis_url=settings.redis_url,
            on_stage=_update_stage,
            job_id=docx_job_id,
        )
    except _StaleJob as se:
        log.info(
            "docx_chapter_task_stale_exit",
            docx_job_id=docx_job_id,
            stage=str(se),
        )
        try:
            (project_dir / f"chapter_{chapter_id}.{docx_job_id}.tmp.docx").unlink(missing_ok=True)
        except Exception:
            log.exception("docx_chapter_tmp_unlink_failed", docx_job_id=docx_job_id)
        return {"status": "stale", "output_path": None}
    except Exception as e:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', error=:e, "
                    "finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:i AND status IN "
                    "('pending','rendering_mermaid','pandoc','finalizing')"
                ),
                {"i": docx_job_id, "e": str(e)[:4000]},
            )
            await s.commit()
        raise

    # 5. finalizing 抢占 + atomic rename + done
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE docx_jobs SET status='finalizing', updated_at=NOW() "
                "WHERE id=:i AND status IN "
                "('pending','rendering_mermaid','pandoc')"
            ),
            {"i": docx_job_id},
        )
        await s.commit()
        if cast(CursorResult[Any], result).rowcount == 0:
            log.warning(
                "docx_chapter_finalize_blocked",
                docx_job_id=docx_job_id,
            )
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                log.exception("docx_chapter_tmp_unlink_failed", tmp=str(tmp_path))
            return {"status": "stale", "output_path": None}

    try:
        tmp_path.rename(final_path)
    except Exception:
        log.exception(
            "docx_chapter_atomic_rename_failed",
            tmp=str(tmp_path),
            final=str(final_path),
        )
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error='atomic rename failed', finished_at=NOW(), "
                    "updated_at=NOW(), output_path=NULL "
                    "WHERE id=:i AND status='finalizing'"
                ),
                {"i": docx_job_id},
            )
            await s.commit()
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)
        raise

    return await _commit_docx_done(docx_job_id, final_path)


# ----- document extract (PR-M7-2) ---------------------------------------


async def extract_document_task(
    ctx: dict[str, Any],
    *,
    document_id: int,
    stored_path: str,
) -> dict[str, Any]:
    """异步抽取上传文档为 markdown + structured_html。

    D-AY:``max_tries=1``——失败后 Document.extract_error 字段记错,UI 提示
    用户重传(不静默重试)。
    """
    del ctx
    path = Path(stored_path)
    if not path.exists():
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE documents SET extract_error=:e WHERE id=:i AND markdown_path IS NULL"
                ),
                {"e": "stored file missing on disk", "i": document_id},
            )
            await s.commit()
        return {"status": "failed", "error": "stored file missing"}

    try:
        # 抽取是同步阻塞 IO(LibreOffice + markitdown),用 to_thread 拿出
        # event loop;arq worker 单 task 跑也不会卡其他 task。
        import asyncio as _asyncio

        md_text = await _asyncio.to_thread(extract_file, path)
    except Exception as e:
        log.exception(
            "extract_document_task_failed",
            document_id=document_id,
            stored_path=stored_path,
        )
        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE documents SET extract_error=:e WHERE id=:i"),
                {
                    "e": f"{type(e).__name__}: {e}"[:2000],
                    "i": document_id,
                },
            )
            await s.commit()
        return {"status": "failed", "error": f"{type(e).__name__}"}

    # 写 .md 文件与 DB 双轨。structured_html 字段拿 markdown 文本兜底,
    # PR-M7-3 在 blackboard 节点会把它转 HTML 后聚合到磁盘黑板。
    md_path = path.with_suffix(".md")
    try:
        import asyncio as _asyncio

        await _asyncio.to_thread(md_path.write_text, md_text, encoding="utf-8")
    except Exception:
        log.exception(
            "extract_document_md_write_failed",
            document_id=document_id,
            md_path=str(md_path),
        )

    async with session_factory() as s:
        await s.execute(
            sa.text(
                "UPDATE documents SET markdown_path=:p, structured_html=:h, "
                "extract_error=NULL WHERE id=:i"
            ),
            {"p": str(md_path), "h": md_text, "i": document_id},
        )
        await s.commit()
    return {"status": "done", "document_id": document_id}
