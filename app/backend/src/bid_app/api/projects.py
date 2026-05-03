"""项目 CRUD + /start + /documents + /outline(§15.1)。

端点:
  · POST   ``/api/projects``                  创建
  · GET    ``/api/projects``                  列表(团队共享池)
  · GET    ``/api/projects/{id}``             详情
  · DELETE ``/api/projects/{id}``             删除(创建者/admin),级联磁盘
  · POST   ``/api/projects/{id}/start``       真快照 ApiKey + Run + try_acquire
  · POST   ``/api/projects/{id}/documents``   上传 + 日配额聚合 + markitdown
  · GET    ``/api/projects/{id}/outline``     拉提纲(P4 渲染)
  · PUT    ``/api/projects/{id}/outline``     提纲确认(D-K resume_review_task)
"""
from __future__ import annotations

import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import sqlalchemy as sa
import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import get_current_user, get_db
from ..models import ApiKey, Chapter, Document, Project, Run, User
from ..schemas.projects import (
    DocumentUploadResponse,
    OutlineConfirmRequest,
    OutlineResponse,
    ProjectCreateRequest,
    ProjectResponse,
    StartRequest,
    StartResponse,
)
from ..services.concurrency import (
    release_project_slot,
    try_acquire_project_slot,
)
from ..services.document_extractor import extract_file

router = APIRouter(prefix="/api/projects", tags=["projects"])
log = structlog.get_logger()

# 上传白名单 + 单文件大小上限(FR-1.4)
_ALLOWED_UPLOAD_EXT = {".docx", ".doc", ".md", ".markdown", ".txt", ".pdf"}
_VALID_DOC_KINDS = {"tech_spec", "scoring", "template"}


# ========== 工具 helper ==========


async def _get_project_or_404(
    db: AsyncSession, project_id: int
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


async def _get_active_run(db: AsyncSession, project_id: int) -> Run:
    row = await db.execute(
        select(Run)
        .where(Run.project_id == project_id)
        .order_by(Run.started_at.desc())
        .limit(1)
    )
    run = row.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no active run for project; did /start succeed?",
        )
    return run


def _project_dir_for(project_id: int) -> Path:
    """项目磁盘目录。``settings.projects_dir`` + ``project_id``。"""
    return Path(settings.projects_dir) / str(project_id)


# ========== CRUD ==========


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Project:
    """创建空项目(状态 init)。需要后续 ``POST /documents`` 上传 3 份文档 +
    ``POST /start`` 启动。"""
    # 占位 dir_path:先 INSERT 拿 id,再 update dir_path
    project = Project(
        name=body.name,
        description=body.description,
        status="init",
        created_by=user.id,
        dir_path="",
        pages_per_chapter=body.pages_per_chapter,
        max_retry_per_chapter=body.max_retry_per_chapter,
    )
    db.add(project)
    await db.flush()
    project.dir_path = str(_project_dir_for(project.id))
    await db.commit()

    # 真磁盘目录 mkdir(失败仅 log,不影响 create)
    try:
        Path(project.dir_path).mkdir(parents=True, exist_ok=True)
    except Exception:
        log.exception("project_dir_mkdir_failed", path=project.dir_path)
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[Project]:
    """团队共享池:列出全部项目(创建者无关)。"""
    rows = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return list(rows.scalars().all())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> Project:
    return await _get_project_or_404(db, project_id)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """FR-1.6:只有创建者 / admin 可删,同步删磁盘目录。"""
    project = await _get_project_or_404(db, project_id)
    if project.created_by != user.id and user.role != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only creator or admin can delete"
        )

    dir_path = Path(project.dir_path) if project.dir_path else None

    await db.delete(project)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    if dir_path is not None and dir_path.exists():
        try:
            shutil.rmtree(dir_path)
        except Exception:
            log.exception("project_dir_rm_failed", path=str(dir_path))

    return {"ok": True}


# ========== Documents 列表 + 上传 ==========


@router.get(
    "/{project_id}/documents",
    response_model=list[DocumentUploadResponse],
)
async def list_documents(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[Document]:
    """列出项目已上传的文档(给 DocumentUploadPage 跨会话回看用)。

    NOTE:不在 REQUIREMENTS §9 显式列出,但 backend ↔ frontend 契约对账时
    确认:用户刷新 / 跨会话回看时需要恢复"已上传记录"。返回顺序按
    ``id ASC``,前端可在 client-side 按 ``kind`` 分桶。
    """
    await _get_project_or_404(db, project_id)
    rows = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.id.asc())
    )
    return list(rows.scalars().all())


@router.post(
    "/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    project_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    kind: Annotated[str, Form(..., description="tech_spec / scoring / template")],
    file: Annotated[UploadFile, File(...)],
) -> Document:
    """上传一份 .docx/.doc/.md/.txt/.pdf,markitdown 抽取后入库。

    校验:
    - kind ∈ {tech_spec, scoring, template}
    - 文件后缀白名单(``_ALLOWED_UPLOAD_EXT``)
    - 单文件 ≤ ``settings.max_file_size_mb``
    - 单用户当日累计 ≤ ``settings.daily_upload_quota_mb``(NFR-4)
    """
    project = await _get_project_or_404(db, project_id)
    if project.status not in ("init", "extracting", "outlining", "outline_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status '{project.status}' does not accept document uploads",
        )

    if kind not in _VALID_DOC_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"kind must be one of {sorted(_VALID_DOC_KINDS)}",
        )

    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported file type {suffix!r}; allowed: "
            f"{sorted(_ALLOWED_UPLOAD_EXT)}",
        )

    # 读流并校验大小(file.size 在 starlette 0.36+ 给的是 Content-Length,
    # 不一定准;以读完后的字节数为准)
    raw = await file.read()
    file_size = len(raw)
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file too large: {file_size // 1024 // 1024}MB > "
            f"{settings.max_file_size_mb}MB",
        )

    # 日配额聚合(本用户、当日、按 settings.tz)
    today_used = (
        await db.execute(
            sa.text(
                "SELECT COALESCE(SUM(d.file_size), 0) FROM documents d "
                "JOIN projects p ON p.id = d.project_id "
                "WHERE p.created_by = :u "
                "AND d.created_at >= "
                "    date_trunc('day', NOW() AT TIME ZONE :tz)"
            ),
            {"u": user.id, "tz": settings.tz},
        )
    ).scalar_one()
    daily_quota_bytes = settings.daily_upload_quota_mb * 1024 * 1024
    if int(today_used) + file_size > daily_quota_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"今日上传配额已用 {int(today_used) // 1024 // 1024}MB,"
            f"上限 {settings.daily_upload_quota_mb}MB",
        )

    # 落盘
    project_dir = Path(project.dir_path)
    uploads_dir = project_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{kind}_{secrets.token_hex(4)}{suffix}"
    stored_path = uploads_dir / safe_name
    stored_path.write_bytes(raw)

    # markitdown 抽取(失败容忍,记 extract_error 字段)
    md_path: Path | None = None
    extract_error: str | None = None
    try:
        md_text = extract_file(stored_path)
        md_path = uploads_dir / f"{kind}.md"
        md_path.write_text(md_text, encoding="utf-8")
    except Exception as e:
        log.exception(
            "document_extract_failed",
            project_id=project_id,
            kind=kind,
            path=str(stored_path),
        )
        extract_error = f"{type(e).__name__}: {e}"

    doc = Document(
        project_id=project_id,
        kind=kind,
        original_filename=file.filename,
        markdown_path=str(md_path) if md_path else None,
        file_size=file_size,
        extract_error=extract_error,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


# ========== /start ==========


@router.post("/{project_id}/start", response_model=StartResponse)
async def start_workflow(
    project_id: int,
    body: StartRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StartResponse:
    """启动工作流(D-AF / D-C 真快照 / D-T 名额)。"""
    project = await _get_project_or_404(db, project_id)

    if project.status != "init":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status is '{project.status}', /start only allowed when init",
        )

    api_key = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id, ApiKey.provider == "dashscope"
            )
        )
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED, "请先配置 DashScope API Key"
        )

    # ⭐ D-C 真快照
    project.api_key_owner = user.id
    project.encrypted_api_key_snapshot = api_key.encrypted_key
    project.pages_per_chapter = body.pages_per_chapter
    project.max_retry_per_chapter = body.max_retry_per_chapter

    thread_id = f"run-{project_id}-{secrets.token_hex(8)}"
    run = Run(
        project_id=project_id,
        langgraph_thread_id=thread_id,
        started_at=datetime.now(UTC),
        status="running",
    )
    db.add(run)
    await db.flush()

    result = await try_acquire_project_slot(project_id)
    if result.reason == "already_active":
        # 上面已校验 status==init,理论不会到这,兜底防 race
        raise HTTPException(
            status.HTTP_409_CONFLICT, "项目已有进行中的执行"
        )

    if result.acquired:
        project.status = "extracting"
    else:  # "full"
        project.status = "queued"
    await db.commit()

    if result.acquired:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            # arq_pool 还没初始化(M2 接 lifespan 挂),回滚 + 503
            await release_project_slot(project_id, result.token)
            project.status = "init"
            run.status = "aborted"
            run.finished_at = datetime.now(UTC)
            run.error = "arq_pool not initialized"
            await db.commit()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "arq_pool 未初始化(等 lifespan 起完再试)",
            )
        try:
            await arq_pool.enqueue_job(
                "start_workflow_task",
                project_id=project_id,
                run_id=run.id,
                thread_id=thread_id,
                slot_token=result.token,
            )
        except Exception as e:
            log.exception(
                "start_enqueue_failed", project_id=project_id, run_id=run.id
            )
            project.status = "init"
            run.status = "aborted"
            run.finished_at = datetime.now(UTC)
            run.error = f"enqueue failed: {e!r}"
            await db.commit()
            await release_project_slot(project_id, result.token)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="无法入队工作流任务,请稍后重试 /start",
            ) from e

    return StartResponse(run_id=run.id, queued=not result.acquired)


# ========== Outline ==========


@router.get("/{project_id}/outline", response_model=OutlineResponse)
async def get_outline(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> OutlineResponse:
    """拉项目最新 run 的提纲(给 P4 渲染)。"""
    project = await _get_project_or_404(db, project_id)
    run = (
        await db.execute(
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(Run.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    chapters: list[dict[str, Any]] = []
    if run is not None:
        rows = (
            await db.execute(
                select(Chapter)
                .where(Chapter.run_id == run.id)
                .order_by(Chapter.index.asc())
            )
        ).scalars().all()
        chapters = [
            {
                "id": f"ch_{c.index + 1:02d}",
                "title": c.title,
                "summary": c.summary,
                "key_points": c.key_points or [],
                "target_pages": c.target_pages,
                "index": c.index,
                "status": c.status,
                # ⭐ R-15 配套:R-14 partial / 完整正文都让 outline 端点暴露,
                # 前端 useProjectOutline 轮询拿到就 hydrate(单端点路径,
                # 不强制额外调 GET /chapters/{idx})。
                "final_text": c.final_text,
            }
            for c in rows
        ]

    return OutlineResponse(
        project_id=project.id,
        run_id=run.id if run else None,
        status=project.status,
        chapters=chapters,
    )


@router.put("/{project_id}/outline")
async def confirm_outline(
    project_id: int,
    body: OutlineConfirmRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """提纲确认(D-K)。``chapters`` 为空 → 自动确认沿用 LLM-1。"""
    project = await _get_project_or_404(db, project_id)
    if project.status != "outline_ready":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status must be outline_ready, got {project.status}",
        )

    # body.chapters 已被 pydantic 校验过 title/key_points/target_pages
    edited = [c.model_dump() for c in body.chapters] if body.chapters else []

    run = await _get_active_run(db, project_id)

    result = await try_acquire_project_slot(project_id)
    if result.reason == "already_active":
        raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有任务在执行")
    if not result.acquired:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="系统繁忙,请稍后重试",
            headers={"Retry-After": "60"},
        )

    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is None:
        await release_project_slot(project_id, result.token)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "arq_pool 未初始化"
        )

    try:
        await arq_pool.enqueue_job(
            "resume_review_task",
            project_id=project_id,
            run_id=run.id,
            thread_id=run.langgraph_thread_id,
            resume_payload={"kind": "outline_confirm", "chapters": edited},
            slot_token=result.token,
            reviewer_id=user.id,
        )
    except Exception as e:
        await release_project_slot(project_id, result.token)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队提纲确认任务,请稍后重试",
        ) from e

    return {"ok": True}


# ========== /proposal(全文整合产物) ==========


@router.get("/{project_id}/proposal")
async def get_proposal_text(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """JSON 形式返回 ``proposal.md`` 内容(给前端 ProposalPage 渲染)。"""
    project = await _get_project_or_404(db, project_id)
    md_path = Path(project.dir_path) / "proposal.md"
    if not md_path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"proposal.md not found; project status='{project.status}',"
            "工作流是否已跑到 assemble?",
        )
    text = md_path.read_text(encoding="utf-8")
    return {
        "project_id": project.id,
        "status": project.status,
        "markdown": text,
        "chars": len(text),
    }


@router.get(
    "/{project_id}/proposal.md",
    response_class=FileResponse,
    response_model=None,  # ⭐ FastAPI 不能从 Union[Response, Response] 推 schema
)
async def download_proposal_md(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """直接下载 ``proposal.md`` 文件(``Content-Disposition`` 含项目名)。"""
    project = await _get_project_or_404(db, project_id)
    md_path = Path(project.dir_path) / "proposal.md"
    if not md_path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "proposal.md not found"
        )
    safe_name = "".join(
        "_" if ch in '<>:"/\\|?*' else ch for ch in project.name
    )[:80] or "proposal"
    return FileResponse(
        md_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{safe_name}.md",
    )
