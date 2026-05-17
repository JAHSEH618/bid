"""章节审核与重试(§15.2 / D-I / D-AD / D-AO / D-AR / D-AC)。

端点:
  · POST /api/projects/{id}/chapters/{idx}/review
      → resume_review_task(D-I);chapter awaiting_review → reviewing 中间态;
        失败补偿全包(回 awaiting_review,释放 slot)。ReviewEvent 由 worker 写。
  · POST /api/projects/{id}/chapters/{idx}/generate
      → resume_review_task;从章节生成确认 interrupt 恢复,进入 LLM-2。
  · POST /api/projects/{id}/chapters/{idx}/retry
      → retry_failed_chapter_task;chapter failed → retrying 中间态;
        失败补偿全包(回 failed,释放 slot)。retry_count=0 / abandoned 由 worker 做。
"""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import get_current_user, get_db
from ..models import Chapter, ChapterVersion, Run, User
from ..schemas.chapters import (
    ChapterDetailResponse,
    ChapterModelUpdateRequest,
    ChapterVersionResponse,
    ReviewRequest,
)
from ..services.concurrency import (
    release_project_slot,
    try_acquire_project_slot,
)
from .me import _available_models_for

router = APIRouter(prefix="/api/projects", tags=["chapters"])
log = structlog.get_logger()


def _normalize_selected_model(model: str | None, user: User) -> str | None:
    selected = (model or "").strip()
    if not selected:
        return None
    if selected not in _available_models_for(user):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"model not in your model catalog: {selected}",
        )
    return selected


def _chapter_generation_limit() -> int:
    """单项目正文生成并发上限,环境变量可降不可升。"""
    return max(1, min(3, int(settings.max_concurrent_chapter_generations or 1)))


_GENERATION_SLOT_STATUSES = {"generating", "retrying"}


async def _get_active_run(db: AsyncSession, project_id: int) -> Run:
    row = await db.execute(
        select(Run).where(Run.project_id == project_id).order_by(Run.started_at.desc()).limit(1)
    )
    run = row.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no active run for project; did /start succeed?",
        )
    return run


# ============== GET 单章详情 ==============


@router.get(
    "/{project_id}/chapters/{idx}",
    response_model=ChapterDetailResponse,
)
async def get_chapter_detail(
    project_id: int,
    idx: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> ChapterDetailResponse:
    """单章详情(R-14 配套):暴露 ``final_text`` 给前端 hydrate。

    - 取项目最新 Run 下 index=idx 的章节
    - ``status='generating'`` 时 ``final_text`` 是 partial 快照(R-14 periodic flush 写入)
    - ``status='awaiting_review'`` / ``approved`` / ``skipped`` 时是完整正文
    - ``current_version_id`` 是最新一条 ``ChapterVersion.id``(给 /review POST 路径关联,
      没有版本时 None)

    路径与现有 ``/chapters/{idx}/review`` / ``/chapters/{idx}/retry`` 同前缀。
    """
    run = await _get_active_run(db, project_id)

    chapter = (
        await db.execute(select(Chapter).where(Chapter.run_id == run.id, Chapter.index == idx))
    ).scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")

    # current_version_id:取该 chapter 最新 ChapterVersion 行(MAX(version))
    cv_row = await db.execute(
        sa.text(
            "SELECT id FROM chapter_versions WHERE chapter_id=:c ORDER BY version DESC LIMIT 1"
        ),
        {"c": chapter.id},
    )
    current_version_id = cv_row.scalar_one_or_none()

    return ChapterDetailResponse(
        id=chapter.id,
        index=chapter.index,
        title=chapter.title,
        status=chapter.status,
        final_text=chapter.final_text,
        chapter_model=chapter.model_snapshot,
        retry_count=chapter.retry_count,
        last_error=chapter.last_error,
        current_version_id=current_version_id,
        updated_at=chapter.created_at,  # 没有 onupdate;暂用 created_at
    )


@router.get(
    "/{project_id}/chapters/{idx}/versions",
    response_model=list[ChapterVersionResponse],
)
async def list_chapter_versions(
    project_id: int,
    idx: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[ChapterVersion]:
    """返回单章历史版本,供 P5 历史版本模式查看与回溯。"""
    run = await _get_active_run(db, project_id)

    chapter = (
        await db.execute(select(Chapter).where(Chapter.run_id == run.id, Chapter.index == idx))
    ).scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")

    rows = await db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == chapter.id)
        .order_by(ChapterVersion.version.desc())
    )
    return list(rows.scalars().all())


@router.patch("/{project_id}/chapters/{idx}/model")
async def update_chapter_model(
    project_id: int,
    idx: int,
    body: ChapterModelUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str | bool | None]:
    """更新单章正文生成模型。

    只允许在章节未进入当前生成链路时修改:
    - pending:首次生成前
    - awaiting_review:提交“不通过/重写”前
    - failed:重新生成前
    """
    selected = _normalize_selected_model(body.chapter_model, user)
    run = await _get_active_run(db, project_id)

    chapter = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, status, model_snapshot FROM chapters "
                    "WHERE run_id=:r AND index=:i FOR UPDATE"
                ),
                {"r": run.id, "i": idx},
            )
        )
        .mappings()
        .one_or_none()
    )
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")
    if chapter["status"] not in ("pending", "awaiting_review", "failed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"chapter is {chapter['status']}, model can only be changed before generation or rewrite",
        )

    clear_prefetch = chapter["status"] == "pending" and selected != chapter["model_snapshot"]
    await db.execute(
        sa.text(
            "UPDATE chapters SET model_snapshot=:m, "
            "final_text=CASE WHEN :clear_prefetch THEN NULL ELSE final_text END "
            "WHERE id=:c"
        ),
        {"m": selected, "clear_prefetch": clear_prefetch, "c": chapter["id"]},
    )
    if clear_prefetch:
        await db.execute(
            sa.text(
                "UPDATE chapter_versions SET abandoned=true "
                "WHERE chapter_id=:c AND decision IS NULL AND abandoned=false"
            ),
            {"c": chapter["id"]},
        )
    await db.commit()
    # 切模型 + 清 prefetch → 旧 chapter_{id}.docx 与新 final_text 错位
    if clear_prefetch:
        from ..services.docx_invalidation import invalidate_chapter_docx

        await invalidate_chapter_docx(int(chapter["id"]))
    return {"ok": True, "chapter_model": selected}


# ============== /review ==============


@router.post("/{project_id}/chapters/{idx}/generate")
async def generate_chapter(
    project_id: int,
    idx: int,
    request: Request,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """触发某个 pending 章节的正文生成。

    当前工作流章节继续走 LangGraph,生成完进入人工审核;非当前章节只生成
    正文缓存,等流程推进到该章时复用。任意时刻同一项目占用正文生成槽的
    章节数不能超过 ``max_concurrent_chapter_generations``。
    """
    run = await _get_active_run(db, project_id)

    rows = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, index, status FROM chapters "
                    "WHERE run_id=:r ORDER BY index ASC FOR UPDATE"
                ),
                {"r": run.id},
            )
        )
        .mappings()
        .all()
    )
    target = next((row for row in rows if row["index"] == idx), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")
    if target["status"] != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"chapter is {target['status']}, only pending can be generated",
        )

    active_count = sum(1 for row in rows if row["status"] in _GENERATION_SLOT_STATUSES)
    limit = _chapter_generation_limit()
    if active_count >= limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"正文生成中的章节已达上限({active_count}/{limit})",
        )

    current = next(
        (row for row in rows if row["status"] not in ("approved", "skipped")),
        None,
    )
    is_current = current is not None and current["index"] == idx
    chapter_id = int(target["id"])

    # API 层先占用章节生成槽,避免多个快速点击在 worker 真正切 generating 前
    # 同时通过阈值校验。
    await db.execute(
        sa.text(
            "UPDATE chapters SET status='generating', "
            "processing_started_at=NOW(), last_error=NULL "
            "WHERE id=:c"
        ),
        {"c": chapter_id},
    )
    await db.commit()

    acquired_token: str | None = None
    try:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")

        if is_current:
            result = await try_acquire_project_slot(project_id)
            if result.reason == "already_active":
                raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有任务在执行,请稍后重试")
            if not result.acquired:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="系统繁忙(并发上限已达),请稍后重试",
                    headers={"Retry-After": "60"},
                )
            acquired_token = result.token

            await arq_pool.enqueue_job(
                "resume_review_task",
                project_id=project_id,
                run_id=run.id,
                thread_id=run.langgraph_thread_id,
                resume_payload={
                    "kind": "chapter_generate",
                    "chapter_index": idx,
                },
                slot_token=acquired_token,
            )
        else:
            await arq_pool.enqueue_job(
                "generate_chapter_body_task",
                project_id=project_id,
                run_id=run.id,
                chapter_index=idx,
            )
    except HTTPException:
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='pending', processing_started_at=NULL "
                "WHERE id=:c AND status='generating'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise
    except Exception as e:
        log.exception(
            "generate_chapter_unexpected_error",
            project_id=project_id,
            chapter_index=idx,
        )
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='pending', processing_started_at=NULL "
                "WHERE id=:c AND status='generating'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "章节生成入队异常,请稍后重试"
        ) from e

    return {"ok": True}


@router.post("/{project_id}/chapters/{idx}/review")
async def review_chapter(
    project_id: int,
    idx: int,
    body: ReviewRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """⭐ D-I + D-AD + D-AO + D-AR + D-AC。"""
    if body.decision == "revise" and not (body.feedback or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "revise must include feedback")

    run = await _get_active_run(db, project_id)

    # 行锁 + 状态校验
    chapter = (
        (
            await db.execute(
                sa.text("SELECT id, status FROM chapters WHERE run_id=:r AND index=:i FOR UPDATE"),
                {"r": run.id, "i": idx},
            )
        )
        .mappings()
        .one_or_none()
    )
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")
    if chapter["status"] != "awaiting_review":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"chapter is {chapter['status']}, only awaiting_review can be reviewed",
        )

    chapter_id = chapter["id"]
    # ⭐ D-AD 中间态 + D-AR processing_started_at
    await db.execute(
        sa.text("UPDATE chapters SET status='reviewing', processing_started_at=NOW() WHERE id=:c"),
        {"c": chapter_id},
    )
    await db.commit()

    acquired_token: str | None = None
    try:
        result = await try_acquire_project_slot(project_id)
        if result.reason == "already_active":
            raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有任务在执行,请稍后重试")
        if not result.acquired:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="系统繁忙(并发上限已达),请稍后重试",
                headers={"Retry-After": "60"},
            )
        acquired_token = result.token

        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")

        await arq_pool.enqueue_job(
            "resume_review_task",
            project_id=project_id,
            run_id=run.id,
            thread_id=run.langgraph_thread_id,
            resume_payload={
                "kind": "chapter_review",
                "decision": body.decision,
                "feedback": body.feedback or "",
            },
            slot_token=acquired_token,
            reviewer_id=user.id,
            chapter_id=chapter_id,
        )
    except HTTPException:
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='awaiting_review', "
                "processing_started_at=NULL "
                "WHERE id=:c AND status='reviewing'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise
    except Exception as e:
        log.exception(
            "review_unexpected_error",
            project_id=project_id,
            chapter_id=chapter_id,
        )
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='awaiting_review', "
                "processing_started_at=NULL "
                "WHERE id=:c AND status='reviewing'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "审核处理异常,请稍后重试") from e

    # ⭐ D-AC:ReviewEvent 由 worker 入口写,API 不再写
    return {"ok": True}


# ============== /retry ==============


@router.post("/{project_id}/chapters/{idx}/retry")
async def retry_chapter(
    project_id: int,
    idx: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """⭐ FR-4.7 + D-AD + D-AO + D-AR。仅 ``status='failed'`` 章节可触发。"""
    run = await _get_active_run(db, project_id)

    rows = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, index, status FROM chapters "
                    "WHERE run_id=:r ORDER BY index ASC FOR UPDATE"
                ),
                {"r": run.id},
            )
        )
        .mappings()
        .all()
    )
    chapter = next((row for row in rows if row["index"] == idx), None)
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")
    if chapter["status"] != "failed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"chapter is {chapter['status']}, not failed",
        )

    active_count = sum(1 for row in rows if row["status"] in _GENERATION_SLOT_STATUSES)
    limit = _chapter_generation_limit()
    if active_count >= limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"正文生成中的章节已达上限({active_count}/{limit})",
        )

    current = next(
        (row for row in rows if row["status"] not in ("approved", "skipped")),
        None,
    )
    is_current = current is not None and current["index"] == idx
    chapter_id = chapter["id"]

    if not is_current:
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='generating', "
                "processing_started_at=NOW(), last_error=NULL "
                "WHERE id=:c"
            ),
            {"c": chapter_id},
        )
        await db.commit()

        try:
            arq_pool = getattr(request.app.state, "arq_pool", None)
            if arq_pool is None:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")
            await arq_pool.enqueue_job(
                "generate_chapter_body_task",
                project_id=project_id,
                run_id=run.id,
                chapter_index=idx,
                reviewer_id=user.id,
                chapter_id=chapter_id,
            )
        except HTTPException:
            await db.execute(
                sa.text(
                    "UPDATE chapters SET status='failed', processing_started_at=NULL "
                    "WHERE id=:c AND status='generating'"
                ),
                {"c": chapter_id},
            )
            await db.commit()
            raise
        except Exception as e:
            log.exception(
                "retry_body_generation_unexpected_error",
                project_id=project_id,
                chapter_id=chapter_id,
            )
            await db.execute(
                sa.text(
                    "UPDATE chapters SET status='failed', processing_started_at=NULL "
                    "WHERE id=:c AND status='generating'"
                ),
                {"c": chapter_id},
            )
            await db.commit()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "重试处理异常,请稍后重试"
            ) from e

        return {"ok": True}

    await db.execute(
        sa.text("UPDATE chapters SET status='retrying', processing_started_at=NOW() WHERE id=:c"),
        {"c": chapter_id},
    )
    await db.commit()

    acquired_token: str | None = None
    try:
        result = await try_acquire_project_slot(project_id)
        if result.reason == "already_active":
            raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有任务在执行,请稍后重试")
        if not result.acquired:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="系统繁忙,请稍后重试",
                headers={"Retry-After": "60"},
            )
        acquired_token = result.token

        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")

        await arq_pool.enqueue_job(
            "retry_failed_chapter_task",
            project_id=project_id,
            run_id=run.id,
            thread_id=run.langgraph_thread_id,
            chapter_index=idx,
            chapter_id=chapter_id,
            reviewer_id=user.id,
            slot_token=acquired_token,
        )
    except HTTPException:
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='failed', "
                "processing_started_at=NULL "
                "WHERE id=:c AND status='retrying'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise
    except Exception as e:
        log.exception(
            "retry_unexpected_error",
            project_id=project_id,
            chapter_id=chapter_id,
        )
        if acquired_token:
            await release_project_slot(project_id, acquired_token)
        await db.execute(
            sa.text(
                "UPDATE chapters SET status='failed', "
                "processing_started_at=NULL "
                "WHERE id=:c AND status='retrying'"
            ),
            {"c": chapter_id},
        )
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "重试处理异常,请稍后重试") from e

    return {"ok": True}
