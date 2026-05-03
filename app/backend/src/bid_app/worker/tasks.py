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
from ..services.document_extractor import extract_for_project
from ..services.docx_export import export_docx
from ..services.llm import ChapterGenerationFailed
from ..workflow.graph import build_graph
from ..workflow.state import WorkflowState


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

# ⭐ D-BX:阶段切换 WHERE 前置守护;每个目标状态的合法前驱
_DOCX_STAGE_ALLOWED_FROM: dict[str, tuple[str, ...]] = {
    "pandoc": ("rendering_mermaid",),
}


async def _commit_docx_done(
    docx_job_id: int, final_path: Path
) -> dict[str, str | None]:
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
                    log.exception(
                        "docx_invalidated_unlink_failed", path=str(final_path)
                    )
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
    """串行锁在 export_docx 内部实现(D-H)。"""
    # 0. ⭐ D-AK:校验 DocxJob row 存在
    async with session_factory() as s:
        existing = (
            await s.execute(
                sa.text("SELECT status FROM docx_jobs WHERE id=:i"),
                {"i": docx_job_id},
            )
        ).scalar_one_or_none()
        if existing is None:
            log.error(
                "docx_job_row_missing",
                docx_job_id=docx_job_id,
                project_id=project_id,
                hint="API 端 commit 可能失败;arq 仍把 task 入了队",
            )
            return {"error": "docx_job row not found"}
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

    # 1. 取项目 markdown + dir + name
    async with session_factory() as s:
        prj_row = await s.execute(
            sa.text(
                "SELECT name, dir_path FROM projects WHERE id=:p"
            ),
            {"p": project_id},
        )
        project_name, project_dir_str = prj_row.one()
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
            raise RuntimeError(
                f"project {project_id} has no completed run"
            )

        md_path = project_dir / "proposal.md"
        if not md_path.exists():
            raise RuntimeError(f"proposal.md missing at {md_path}")
        markdown = md_path.read_text(encoding="utf-8")

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
            (
                project_dir / f"proposal.{docx_job_id}.tmp.docx"
            ).unlink(missing_ok=True)
        except Exception:
            log.exception(
                "docx_tmp_unlink_failed", docx_job_id=docx_job_id
            )
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
                log.exception(
                    "docx_tmp_unlink_failed", tmp=str(tmp_path)
                )
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
