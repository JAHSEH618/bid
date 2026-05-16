"""DOCX 生成与下载(§15.3)。

端点:
  · POST /api/projects/{id}/proposal.docx        触发生成(D-AK / D-CJ / D-CK)
  · GET  /api/projects/{id}/docx-job/{docx_job_id}  轮询进度(D-BW + D-CD inline repair)
  · GET  /api/projects/{id}/proposal.docx        下载(D-L 固定缓存名 +
                                                   D-CJ 拒分支 + D-CO inline repair)

修复点:
- D-L:缓存路径**固定** ``{project_dir}/proposal.docx``;展示文件名 走
  ``Content-Disposition``(``项目名_技术方案_YYYYMMDD.docx``)
- D-AK:入队前**先 commit pending 行**,再 enqueue,最后 UPDATE arq_job_id
  (避免 enqueue 成功但 commit 失败时 worker 找不到 row)
- D-CJ:下载放行不只看文件存在,还要 latest DocxJob.status == 'done';
  invalidated 走 409 "请重新生成",其它 in-flight 走 409 "未就绪"
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import get_current_user, get_db
from ..models import Chapter, DocxJob, Project, User
from ..services.docx_export import sanitize_filename

router = APIRouter(prefix="/api/projects", tags=["docx"])
chapter_router = APIRouter(prefix="/api/chapters", tags=["chapter-docx"])
log = structlog.get_logger()


def _display_filename(project_name: str) -> str:
    """FR-5.6:``{project_name}_技术方案_{YYYYMMDD}.docx``,YYYYMMDD 用 ``Asia/Shanghai``。"""
    today = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d")
    return f"{sanitize_filename(project_name)}_技术方案_{today}.docx"


async def _get_done_project(db: AsyncSession, project_id: int) -> Project:
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if p.status != "done":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project not done yet, status={p.status}",
        )
    return p


# ============== POST 触发 ==============


@router.post("/{project_id}/proposal.docx")
async def trigger_docx(
    project_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """触发 DOCX 生成。命中缓存返 ``cached: true`` + latest done docx_job_id。"""
    project = await _get_done_project(db, project_id)

    cached = Path(project.dir_path) / "proposal.docx"

    # ⭐ D-BY:命中缓存前先 repair 任何"finalizing 但文件已就位"的孤儿 job
    # ⭐ scope='project':防止把单章 finalizing job 错刷成整本路径
    if cached.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='done', output_path=:p, "
                "finished_at=NOW(), updated_at=NOW() "
                "WHERE project_id=:pid AND scope='project' "
                "AND status='finalizing'"
            ),
            {"p": str(cached), "pid": project_id},
        )
        await db.commit()

    # ⭐ D-CJ:仅看文件存在不够 — invalidated 状态下旧文件可能残留
    # ⭐ scope='project':只看整本 job,避免被刚跑的单章 job 顶上去
    latest = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, status FROM docx_jobs "
                    "WHERE project_id=:p AND scope='project' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"p": project_id},
            )
        )
        .mappings()
        .one_or_none()
    )

    if cached.exists() and latest and latest["status"] == "done":
        # ⭐ D-CK:cached=True 时返回 latest done 的 docx_job_id,前端有轮询入口
        return {
            "docx_job_id": latest["id"],
            "arq_job_id": None,
            "cached": True,
        }

    # ⭐ D-AK 顺序:先 commit pending 行,再 enqueue,最后 UPDATE arq_job_id
    docx_job = DocxJob(project_id=project_id, arq_job_id=None, status="pending")
    db.add(docx_job)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有 DOCX 生成任务在进行中") from e
    job_pk = docx_job.id

    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is None:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='arq_pool not initialized', finished_at=NOW(), "
                "updated_at=NOW() WHERE id=:i"
            ),
            {"i": job_pk},
        )
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")

    try:
        job = await arq_pool.enqueue_job(
            "generate_docx_task",
            project_id=project_id,
            docx_job_id=job_pk,
        )
    except Exception as e:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', error=:err, "
                "finished_at=NOW(), updated_at=NOW() WHERE id=:i"
            ),
            {"err": f"enqueue failed: {e!r}"[:4000], "i": job_pk},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队 DOCX 任务,请稍后重试",
        ) from e

    if job is None:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='enqueue returned None', finished_at=NOW(), "
                "updated_at=NOW() WHERE id=:i"
            ),
            {"i": job_pk},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队 DOCX 任务,请稍后重试",
        )

    # 回写 arq_job_id;失败 OK,worker 仍能用 job_pk 找 row
    docx_job_2 = await db.get(DocxJob, job_pk)
    if docx_job_2 is not None:
        docx_job_2.arq_job_id = job.job_id
        await db.commit()

    return {"docx_job_id": job_pk, "arq_job_id": job.job_id, "cached": False}


# ============== GET 进度 ==============


@router.get("/{project_id}/docx-job/{docx_job_id}")
async def get_docx_job(
    project_id: int,
    docx_job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """轮询 DOCX 任务进度(D-BW + D-CD inline repair)。"""
    row_raw = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, project_id, status, error, output_path, "
                    "scope, chapter_id, "
                    "created_at, updated_at, finished_at "
                    "FROM docx_jobs WHERE id=:i AND project_id=:p"
                ),
                {"i": docx_job_id, "p": project_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row_raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "docx job not found")
    # 拷贝成可变 dict;后续 inline-repair 需要覆盖 status / output_path
    # 等字段,RowMapping 是 read-only。
    row: dict[str, Any] = dict(row_raw)

    # ⭐ D-CD:轮询路径上 inline finalizing repair
    # ⭐ 按 scope 决定要 repair 的文件:整本 → proposal.docx;单章 → chapter_{id}.docx
    if row["status"] == "finalizing":
        proj_row = await db.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"),
            {"p": project_id},
        )
        dir_path = proj_row.scalar_one_or_none()
        if dir_path:
            if row["scope"] == "chapter" and row["chapter_id"] is not None:
                file_path = Path(dir_path) / f"chapter_{row['chapter_id']}.docx"
            else:
                file_path = Path(dir_path) / "proposal.docx"
            if file_path.exists():
                upd = await db.execute(
                    sa.text(
                        "UPDATE docx_jobs SET status='done', "
                        "output_path=:p, finished_at=NOW(), updated_at=NOW() "
                        "WHERE id=:i AND status='finalizing' "
                        "RETURNING id, status, output_path, finished_at, updated_at"
                    ),
                    {"i": docx_job_id, "p": str(file_path)},
                )
                repaired = upd.mappings().first()
                if repaired:
                    await db.commit()
                    log.info(
                        "docx_finalizing_repaired_inline",
                        docx_job_id=docx_job_id,
                        project_id=project_id,
                    )
                    row.update(
                        {
                            "status": repaired["status"],
                            "output_path": repaired["output_path"],
                            "finished_at": repaired["finished_at"],
                            "updated_at": repaired["updated_at"],
                        }
                    )

    # 内部 → 前端的 status 映射:不暴露 finalizing(D-BU 实现层细节)
    raw = row["status"]
    public_status = (
        "processing" if raw in ("pending", "rendering_mermaid", "pandoc", "finalizing") else raw
    )  # done | failed | invalidated(D-CG)
    progress_hint = {
        "pending": "排队中",
        "rendering_mermaid": "渲染流程图...",
        "pandoc": "转换文档...",
        "finalizing": "收尾中...",
        "done": "已完成",
        "failed": "失败",
        "invalidated": "原文档已更新,请重新生成 DOCX",
    }.get(raw, raw)

    return {
        "docx_job_id": row["id"],
        "status": public_status,
        "stage": progress_hint,
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


# ============== GET 下载 ==============


@router.get(
    "/{project_id}/proposal.docx",
    response_class=FileResponse,
    response_model=None,
)
async def download_docx(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """下载 ``proposal.docx``(D-L 固定缓存名 + D-CJ 拒分支 + D-CO inline repair)。"""
    project = await _get_done_project(db, project_id)
    path = Path(project.dir_path) / "proposal.docx"

    # ⭐ D-CO:下载端 inline finalizing repair
    # ⭐ scope='project':下载整本时只 repair 整本 finalizing,别动单章
    if path.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='done', output_path=:p, "
                "finished_at=NOW(), updated_at=NOW() "
                "WHERE project_id=:pid AND scope='project' "
                "AND status='finalizing'"
            ),
            {"p": str(path), "pid": project_id},
        )
        await db.commit()

    # ⭐ D-CJ:文件存在 ≠ 可下载;先查 latest DocxJob 状态把关
    # ⭐ scope='project':避免被刚跑的单章 job 干扰整本下载放行判断
    latest = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, status FROM docx_jobs "
                    "WHERE project_id=:p AND scope='project' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"p": project_id},
            )
        )
        .mappings()
        .one_or_none()
    )

    # 拒分支 1:latest=invalidated
    if latest and latest["status"] == "invalidated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "docx_invalidated",
                "message": "原文档已更新,请重新生成 DOCX",
                "docx_job_id": latest["id"],
            },
        )

    # 拒分支 2:latest 不是 done
    if not latest or latest["status"] != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "docx_not_ready",
                "message": "请先 POST 触发生成",
                "docx_job_id": latest["id"] if latest else None,
                "current_status": latest["status"] if latest else None,
            },
        )

    # latest=done 但文件不在 → 自动 repair 为 failed
    if not path.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='done file missing on disk', finished_at=NOW(), "
                "updated_at=NOW(), output_path=NULL WHERE id=:i"
            ),
            {"i": latest["id"]},
        )
        await db.commit()
        log.warning(
            "docx_done_file_missing_repaired",
            project_id=project_id,
            docx_job_id=latest["id"],
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "docx_missing",
                "message": "DOCX 文件丢失,请重新生成",
                "docx_job_id": latest["id"],
            },
        )

    fname = _display_filename(project.name)
    ascii_fallback = "proposal.docx"
    return FileResponse(
        path,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}"
            ),
        },
    )


# ============== PR-M6-2:单章 DOCX 导出 ==============
#
# POST /api/chapters/{chapter_id}/export.docx → 触发(返回 docx_job_id)
# GET  /api/chapters/{chapter_id}/export.docx → 下载
#
# 进度查询复用 GET /api/projects/{project_id}/docx-job/{docx_job_id}
# (job id 是全局唯一,project_id 可从 DocxJob 行反查)。


def _display_chapter_filename(project_name: str, chapter_title: str) -> str:
    """``{项目名}_{章节标题}_{YYYYMMDD}.docx``,与全本格式对齐(D-L 风格)。"""
    today = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d")
    return f"{sanitize_filename(project_name)}_{sanitize_filename(chapter_title)}_{today}.docx"


async def _load_chapter_for_export(db: AsyncSession, chapter_id: int) -> tuple[Chapter, Project]:
    """取章节 + 关联项目;鉴权交给 deps.get_current_user。

    导出门槛:有 ``final_text`` 即可。原先只放行 ``approved`` 章节,但用户
    经常想在工作流走到这一章之前(prefetch 完成 / awaiting_review 但还没决策)
    就把已生成的版本单章导出留底。导出是只读操作,不动 DB,后续 workflow 在
    这一章上跑 visual/merge 写出的最终版本不受影响。
    """
    row = (
        (
            await db.execute(
                sa.text(
                    "SELECT c.id, c.status, c.title, c.final_text, "
                    "p.id AS project_id, p.name, p.dir_path "
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
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chapter not found")
    if not row["final_text"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"chapter has no final text yet (status={row['status']})",
        )
    # 实例化轻量对象,避免再次 query
    chapter = Chapter()
    chapter.id = row["id"]
    chapter.title = row["title"]
    chapter.status = row["status"]
    project = Project()
    project.id = row["project_id"]
    project.name = row["name"]
    project.dir_path = row["dir_path"]
    return chapter, project


@chapter_router.post("/{chapter_id}/export.docx")
async def trigger_chapter_docx(
    chapter_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """触发单章 DOCX 生成。命中已存在的 done job 时返回 ``cached=True``。"""
    _chapter, project = await _load_chapter_for_export(db, chapter_id)
    cached = Path(project.dir_path) / f"chapter_{chapter_id}.docx"

    # finalizing repair (与项目级路径同思路)
    if cached.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='done', output_path=:p, "
                "finished_at=NOW(), updated_at=NOW() "
                "WHERE chapter_id=:c AND status='finalizing'"
            ),
            {"p": str(cached), "c": chapter_id},
        )
        await db.commit()

    latest = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, status FROM docx_jobs "
                    "WHERE chapter_id=:c AND scope='chapter' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"c": chapter_id},
            )
        )
        .mappings()
        .one_or_none()
    )

    if cached.exists() and latest and latest["status"] == "done":
        return {
            "docx_job_id": latest["id"],
            "arq_job_id": None,
            "cached": True,
            "scope": "chapter",
        }

    job = DocxJob(
        project_id=project.id,
        scope="chapter",
        chapter_id=chapter_id,
        arq_job_id=None,
        status="pending",
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "本章节已有 DOCX 生成任务在进行中",
        ) from e
    job_pk = job.id

    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is None:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='arq_pool not initialized', finished_at=NOW(), "
                "updated_at=NOW() WHERE id=:i"
            ),
            {"i": job_pk},
        )
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化")

    try:
        arq_job = await arq_pool.enqueue_job(
            "generate_docx_task",
            project_id=project.id,
            docx_job_id=job_pk,
        )
    except Exception as e:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', error=:err, "
                "finished_at=NOW(), updated_at=NOW() WHERE id=:i"
            ),
            {"err": f"enqueue failed: {e!r}"[:4000], "i": job_pk},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队 DOCX 任务,请稍后重试",
        ) from e

    if arq_job is None:
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='enqueue returned None', finished_at=NOW(), "
                "updated_at=NOW() WHERE id=:i"
            ),
            {"i": job_pk},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队 DOCX 任务,请稍后重试",
        )

    job_2 = await db.get(DocxJob, job_pk)
    if job_2 is not None:
        job_2.arq_job_id = arq_job.job_id
        await db.commit()

    return {
        "docx_job_id": job_pk,
        "arq_job_id": arq_job.job_id,
        "cached": False,
        "scope": "chapter",
        "project_id": project.id,
    }


@chapter_router.get(
    "/{chapter_id}/export.docx",
    response_class=FileResponse,
    response_model=None,
)
async def download_chapter_docx(
    chapter_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """下载单章 DOCX。"""
    chapter, project = await _load_chapter_for_export(db, chapter_id)
    path = Path(project.dir_path) / f"chapter_{chapter_id}.docx"

    if path.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='done', output_path=:p, "
                "finished_at=NOW(), updated_at=NOW() "
                "WHERE chapter_id=:c AND status='finalizing'"
            ),
            {"p": str(path), "c": chapter_id},
        )
        await db.commit()

    latest = (
        (
            await db.execute(
                sa.text(
                    "SELECT id, status FROM docx_jobs "
                    "WHERE chapter_id=:c AND scope='chapter' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"c": chapter_id},
            )
        )
        .mappings()
        .one_or_none()
    )

    if not latest or latest["status"] != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "docx_not_ready",
                "message": "请先 POST 触发生成",
                "docx_job_id": latest["id"] if latest else None,
                "current_status": latest["status"] if latest else None,
            },
        )

    if not path.exists():
        await db.execute(
            sa.text(
                "UPDATE docx_jobs SET status='failed', "
                "error='done file missing on disk', finished_at=NOW(), "
                "updated_at=NOW(), output_path=NULL WHERE id=:i"
            ),
            {"i": latest["id"]},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "docx_missing",
                "message": "DOCX 文件丢失,请重新生成",
                "docx_job_id": latest["id"],
            },
        )

    fname = _display_chapter_filename(project.name, chapter.title)
    ascii_fallback = f"chapter_{chapter_id}.docx"
    return FileResponse(
        path,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}"
            ),
        },
    )
