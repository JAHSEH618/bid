"""arq workflow tasks(§10.5,D-Z / D-AY / D-AB / D-AC / D-AT / D-AU /
D-AW / D-AZ / D-BA / D-BK / D-BS / D-BT)。

四类任务,**全部 max_tries=1**(D-Z / D-AY):
  · ``start_workflow_task`` — /start 端点,新启动
  · ``resume_review_task``  — /review / /confirm-outline 端点,从 interrupt 恢复
  · ``retry_failed_chapter_task`` — /chapters/{idx}/retry 端点,失败章节续跑
  · ``generate_docx_task``  — /docx POST 端点(M3-2 真实现,本文件占位)
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog
from arq.worker import func
from langgraph.types import Command

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
from ..services.document_extractor import extract_for_project
from ..services.llm import ChapterGenerationFailed
from ..workflow.graph import build_graph
from ..workflow.state import WorkflowState

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


async def _fail_project_and_run(
    project_id: int, run_id: int, error: str
) -> None:
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
            sa.text(
                "SELECT pages_per_chapter, max_retry_per_chapter "
                "FROM projects WHERE id=:p"
            ),
            {"p": project_id},
        )
        prj = row.one()

    docs = await extract_for_project(project_id)
    return {
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
    }


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
            log.exception(
                "slot_lost_project_status_failed", project_id=project_id
            )
    else:
        log.warning(
            "slot_lost_no_chapter_rolled_back",
            project_id=project_id,
            action=action,
            hint="left for reconcile to mark failed",
        )


# ----- workflow tasks ---------------------------------------------------


@func(max_tries=1)
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
            async for _ in graph.astream(
                initial, config, stream_mode="values"
            ):
                if lost_event.is_set() or not await ensure_project_slot(
                    project_id, token
                ):
                    raise SlotLost(
                        f"slot lost during start, project_id={project_id}"
                    )
    except SlotLost:
        await _slot_lost_compensation(
            project_id,
            run_id,
            current_chapter_id=None,
            action="start",
        )
        log.warning(
            "start_workflow_task_aborted_slot_lost", project_id=project_id
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
        await _fail_project_and_run(
            project_id, run_id, "start_workflow_task crashed"
        )
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
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
                    if review_decision == "revise":
                        # ⭐ D-BK:revise 切 generating 同时写 processing_started_at
                        await s.execute(
                            sa.text(
                                "UPDATE chapters SET status='generating', "
                                "processing_started_at=NOW() "
                                "WHERE id=:c AND status='reviewing'"
                            ),
                            {"c": chapter_id},
                        )
                    await s.commit()

        async with project_heartbeat(project_id, token) as lost_event:
            async for _ in graph.astream(
                Command(resume=resume_payload),
                config,
                stream_mode="values",
            ):
                if lost_event.is_set() or not await ensure_project_slot(
                    project_id, token
                ):
                    raise SlotLost(
                        f"slot lost during resume, project_id={project_id}"
                    )
    except SlotLost:
        await _slot_lost_compensation(
            project_id,
            run_id,
            current_chapter_id=chapter_id,
            action="resume",
            decision=review_decision,
            review_event_id=review_event_id,
        )
        log.warning(
            "resume_review_task_aborted_slot_lost", project_id=project_id
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
        await _fail_project_and_run(
            project_id, run_id, "resume_review_task crashed"
        )
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


@func(max_tries=1)
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
        # ⭐ D-AC + D-AD + FR-4.7:写 ReviewEvent + 把章节从 retrying → pending +
        # 当前未审版本 abandoned=true
        async with session_factory() as s:
            s.add(
                ReviewEvent(
                    chapter_id=chapter_id,
                    reviewer_id=reviewer_id,
                    decision="retry_failed",
                )
            )
            await s.execute(
                sa.text(
                    "UPDATE chapter_versions SET abandoned=true "
                    "WHERE chapter_id=:c AND abandoned=false"
                ),
                {"c": chapter_id},
            )
            # ⭐ D-BS:切 pending 同时写 processing_started_at
            await s.execute(
                sa.text(
                    "UPDATE chapters SET status='pending', retry_count=0, "
                    "last_error=NULL, processing_started_at=NOW() "
                    "WHERE id=:c AND status='retrying'"
                ),
                {"c": chapter_id},
            )
            await s.commit()

        async with project_heartbeat(project_id, token) as lost_event:
            await graph.aupdate_state(config, {"retry_count": 0})
            async for _ in graph.astream(None, config, stream_mode="values"):
                if lost_event.is_set() or not await ensure_project_slot(
                    project_id, token
                ):
                    raise SlotLost(
                        f"slot lost during retry, project_id={project_id}"
                    )
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
        await _fail_project_and_run(
            project_id, run_id, "retry_failed_chapter_task crashed"
        )
        raise
    finally:
        await release_project_slot(project_id, token)
        await wake_queued_projects(arq_pool)


# ----- generate_docx_task (M3-2 真实现) ---------------------------------


@func(max_tries=1)
async def generate_docx_task(
    ctx: dict[str, Any],
    *,
    project_id: int,
    docx_job_id: int,
) -> None:
    """生成 DOCX(M3-2 #21 真实现)。

    M1 占位:把 DocxJob 标 failed + 提示"M3 未完成",而不是直接 raise(避免
    arq 显示 task failed)。M3-2 用 §13.3 完整实现替换,真正调
    ``services.docx_export.export_docx`` + atomic rename + DocxJob 状态机。
    """
    log.warning(
        "generate_docx_task_stub",
        project_id=project_id,
        docx_job_id=docx_job_id,
        hint="M3-2 (#21) 实现真逻辑;现在仅标 DocxJob failed 防止前端无限轮询",
    )
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='failed', "
                    "error='generate_docx_task not yet implemented (M3-2)', "
                    "finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=:j AND status NOT IN ('done','failed','invalidated')"
                ),
                {"j": docx_job_id},
            )
            await s.commit()
    except Exception:
        log.exception(
            "generate_docx_task_stub_db_update_failed",
            docx_job_id=docx_job_id,
        )
