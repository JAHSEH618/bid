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

import asyncio
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

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
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import get_current_user, get_db
from ..models import ApiKey, Chapter, Document, Project, Run, User
from ..schemas.projects import (
    DocumentUploadResponse,
    OutlineChapterDTO,
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
from .me import _available_models_for

router = APIRouter(prefix="/api/projects", tags=["projects"])
log = structlog.get_logger()

# 上传白名单 + 单文件大小上限(FR-1.4)
# ⚠️ .pdf:markitdown 自带 pdfminer 能抽文字型 PDF;扫描型 / 加密型 PDF 会
# 抽不出来 → ``DocumentExtractError`` → 写到 ``Document.extract_error``,前端
# 角标提示用户重传清晰版本(不会让流水线静默崩溃)。
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


def _project_to_response(
    project: Project, username: str | None
) -> ProjectResponse:
    """ORM Project + JOIN 出来的 username → API 响应。

    显式构造而不是 ``model_validate(project)``,避免 ProjectResponse 漏掉
    JOIN 字段 ``created_by_username``;同时把内部专属字段 (dir_path /
    encrypted_api_key_snapshot) 挡在 schema 外。
    """
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        created_by=project.created_by,
        created_by_username=username,
        pages_per_chapter=project.pages_per_chapter,
        max_retry_per_chapter=project.max_retry_per_chapter,
        created_at=project.created_at,
    )


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


# ========== CRUD ==========


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
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
    return _project_to_response(project, user.username)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[ProjectResponse]:
    """团队共享池:列出全部项目(创建者无关)。"""
    rows = await db.execute(
        select(Project, User.username)
        .join(User, User.id == Project.created_by)
        .order_by(Project.created_at.desc())
    )
    return [_project_to_response(p, username) for p, username in rows.all()]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> ProjectResponse:
    project = await _get_project_or_404(db, project_id)
    username = (
        await db.execute(
            select(User.username).where(User.id == project.created_by)
        )
    ).scalar_one_or_none()
    return _project_to_response(project, username)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """FR-1.6:只有创建者 / admin 可删,同步删磁盘目录。

    PR-M7-3 / D2:删除前显式调 ``delete_blackboard`` 清理黑板文件 + DB
    引用。下面 ``shutil.rmtree(dir_path)`` 也会把目录端清掉,这里走 service
    路径主要是 DB-side 字段清空 + log 信号(future event listener 兜底用)。
    """
    project = await _get_project_or_404(db, project_id)
    if project.created_by != user.id and user.role != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only creator or admin can delete"
        )

    dir_path = Path(project.dir_path) if project.dir_path else None

    # ⭐ PR-M7-3:黑板级联删除(在 Project ORM 删除前调,顺序无关但语义更清晰)
    from ..workflow.blackboard import delete_blackboard

    try:
        await delete_blackboard(project_id)
    except Exception:
        log.exception(
            "blackboard_delete_failed_non_fatal", project_id=project_id
        )

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
) -> list[DocumentUploadResponse]:
    """列出项目已上传的文档(给 DocumentUploadPage 跨会话回看用)。

    NOTE:不在 REQUIREMENTS §9 显式列出,但 backend ↔ frontend 契约对账时
    确认:用户刷新 / 跨会话回看时需要恢复"已上传记录"。返回顺序按
    ``id ASC``,前端可在 client-side 按 ``kind`` 分桶。

    PR-M7-2:返回字段加 ``extract_status``,前端轮询用。
    """
    await _get_project_or_404(db, project_id)
    rows = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.id.asc())
    )
    return [_document_to_response(d) for d in rows.scalars().all()]


@router.post(
    "/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    project_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
    kind: Annotated[
        str | None,
        Form(description="可选;v1 三选一保留做向后兼容,v2 起以 tags 为主"),
    ] = None,
    tags: Annotated[
        str | None,
        Form(
            description=(
                "用户自定义标签,逗号分隔。例如 'tech_spec, draft'。"
            ),
        ),
    ] = None,
) -> DocumentUploadResponse:
    """上传一份文档。PR-M7-2 / D5:

    - kind 可选;v1 三选一不再强制,改用 ``tags`` 自定义分类。
    - 单文件 ≤ ``settings.max_file_upload_bytes`` (默认 200MB)。
    - 项目总和 ≤ ``settings.max_project_upload_bytes`` (默认 500MB)。
    - 写盘后立即 202 返回 + ``extract_status='pending'``;
      抽取由 ``extract_document_task`` 后台处理,前端轮询 ``GET /documents``
      看 ``extract_status`` 翻 done / failed。
    """
    project = await _get_project_or_404(db, project_id)
    if project.status not in ("init", "extracting", "outlining", "outline_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status '{project.status}' does not accept document uploads",
        )

    if kind is not None and kind not in _VALID_DOC_KINDS:
        # ⭐ PR-M7-2:kind 仍允许传入(老 UI 兼容),但只校验取值集合
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"kind must be one of {sorted(_VALID_DOC_KINDS)} or omitted",
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
    # ⭐ PR-M7-2 / D5:单文件 200MB
    if file_size > settings.max_file_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file too large: {file_size // 1024 // 1024}MB > "
            f"{settings.max_file_upload_bytes // 1024 // 1024}MB",
        )

    # ⭐ PR-M7-2 / D5:项目总和 500MB
    project_total = (
        await db.execute(
            sa.text(
                "SELECT COALESCE(SUM(COALESCE(byte_size, file_size)), 0) "
                "FROM documents WHERE project_id=:p"
            ),
            {"p": project_id},
        )
    ).scalar_one()
    if int(project_total) + file_size > settings.max_project_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"项目总上传 {int(project_total) // 1024 // 1024}MB 已接近上限,"
            f"再传本文件 ({file_size // 1024 // 1024}MB) 会超过 "
            f"{settings.max_project_upload_bytes // 1024 // 1024}MB",
        )

    # 落盘
    project_dir = Path(project.dir_path)
    uploads_dir = project_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    name_prefix = kind or "doc"
    safe_name = f"{name_prefix}_{secrets.token_hex(4)}{suffix}"
    stored_path = uploads_dir / safe_name
    await asyncio.to_thread(stored_path.write_bytes, raw)

    parsed_tags: list[str] | None = None
    if tags:
        parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] or None

    doc = Document(
        project_id=project_id,
        kind=kind,
        original_filename=file.filename,
        markdown_path=None,
        file_size=file_size,
        byte_size=file_size,
        mime_type=file.content_type or None,
        tags=parsed_tags,
        extract_error=None,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # ⭐ PR-M7-2:把抽取异步化,不阻塞 HTTP 响应
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is not None:
        try:
            await arq_pool.enqueue_job(
                "extract_document_task",
                document_id=doc.id,
                stored_path=str(stored_path),
            )
        except Exception:
            # 入队失败不阻挡 201 — 用户可手动 retry(后续 PR 加端点)
            log.exception(
                "extract_document_enqueue_failed",
                document_id=doc.id,
                project_id=project_id,
            )
    else:
        # arq_pool 缺失时退化:原地同步抽取一次,保证向后兼容
        try:
            md_text = await asyncio.to_thread(extract_file, stored_path)
            md_path = uploads_dir / f"{name_prefix}_{doc.id}.md"
            await asyncio.to_thread(md_path.write_text, md_text, encoding="utf-8")
            doc.markdown_path = str(md_path)
            doc.structured_html = md_text
        except Exception as e:
            log.exception(
                "document_extract_failed_inline",
                project_id=project_id,
                document_id=doc.id,
            )
            doc.extract_error = f"{type(e).__name__}: {e}"
        await db.commit()
        await db.refresh(doc)

    _ = user
    return _document_to_response(doc)


def _document_to_response(doc: Document) -> DocumentUploadResponse:
    """计算 ``extract_status``:有 markdown_path 或 structured_html → done;
    有 extract_error → failed;否则 pending(task 还在跑)。"""
    if doc.extract_error:
        extract_status: Literal["pending", "done", "failed"] = "failed"
    elif doc.markdown_path or doc.structured_html:
        extract_status = "done"
    else:
        extract_status = "pending"
    return DocumentUploadResponse(
        id=doc.id,
        project_id=doc.project_id,
        kind=doc.kind,
        original_filename=doc.original_filename,
        file_size=doc.file_size,
        byte_size=doc.byte_size,
        mime_type=doc.mime_type,
        tags=doc.tags,
        extract_error=doc.extract_error,
        extract_status=extract_status,
    )


@router.delete(
    "/{project_id}/documents/{document_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_document(
    project_id: int,
    document_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    """删除单个上传文档(PR-M7-2 多文件模式)。

    与上传同步:只在工作流未启动的几个状态下允许删除,避免抽取中 / 已下游
    使用的文档被悄悄抽掉。磁盘上的 markdown 副本尽力删,失败仅 log;原始
    上传文件目前未在 Document 行里持久化路径,留待项目删除时 ``rmtree``
    统一清理。
    """
    project = await _get_project_or_404(db, project_id)
    if project.status not in ("init", "extracting", "outlining", "outline_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status '{project.status}' does not accept document edits",
        )

    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")

    if doc.markdown_path:
        try:
            Path(doc.markdown_path).unlink(missing_ok=True)
        except Exception:
            log.exception(
                "document_markdown_unlink_failed",
                project_id=project_id,
                document_id=document_id,
                path=doc.markdown_path,
            )

    await db.delete(doc)
    await db.commit()
    _ = user
    return {"ok": True}


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

    # 模型选择在生成流程里提交。Project 只存提纲 / 配图 / 章节默认快照;
    # 每章正文模型会在确认提纲时写 chapters.model_snapshot。
    project.outline_model_snapshot = _normalize_selected_model(
        body.outline_model, user
    )
    project.chapter_model_snapshot = _normalize_selected_model(
        body.chapter_model, user
    )
    project.visuals_model_snapshot = _normalize_selected_model(
        body.visuals_model, user
    )

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

    chapters: list[OutlineChapterDTO] = []
    if run is not None:
        rows = (
            await db.execute(
                select(Chapter)
                .where(Chapter.run_id == run.id)
                .order_by(Chapter.index.asc())
            )
        ).scalars().all()
        chapters = [
            OutlineChapterDTO(
                id=f"ch_{c.index + 1:02d}",
                section=c.section,
                title=c.title,
                summary=c.summary,
                key_points=c.key_points or [],
                target_pages=c.target_pages,
                index=c.index,
                status=c.status,
                chapter_model=c.model_snapshot,
                # ⭐ R-15 配套:R-14 partial / 完整正文都让 outline 端点暴露,
                # 前端 useProjectOutline 轮询拿到就 hydrate(单端点路径,
                # 不强制额外调 GET /chapters/{idx})。
                final_text=c.final_text,
            )
            for c in rows
        ]

    return OutlineResponse(
        project_id=project.id,
        run_id=run.id if run else None,
        status=project.status,
        max_concurrent_chapter_generations=max(
            1, min(3, int(settings.max_concurrent_chapter_generations or 1))
        ),
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
    available_models = set(_available_models_for(user))
    edited = [c.model_dump() for c in body.chapters] if body.chapters else []
    for chapter in edited:
        model = (chapter.get("chapter_model") or "").strip()
        chapter["chapter_model"] = model or None
        if model and model not in available_models:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"chapter model not in your model catalog: {model}",
            )

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
            resume_payload={
                "kind": "outline_confirm",
                "chapters": edited,
                # PR-M9-1:把用户勾选的章节 id 一并传下游;空 / None → 全选
                "selected_chapter_ids": body.selected_chapter_ids or None,
            },
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


# ========== /material-understanding (PR-M8-1) ==========


@router.get("/{project_id}/material-understanding")
async def get_material_understanding(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """读取 LLM-0 输出的材料理解 JSON。

    数据来源:LangGraph checkpoint 的 ``state.material_understanding``。
    本期 MVP 走 SSE 推送 + 前端缓存,不开 DB 查询;若 checkpoint 不可用
    则回 404 让用户重跑或刷新。
    """
    project = await _get_project_or_404(db, project_id)

    saver = getattr(_router_app_state(), "checkpointer", None)
    if saver is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "checkpointer 未初始化,请稍后重试",
        )
    run = await _get_active_run(db, project_id)
    config = {"configurable": {"thread_id": run.langgraph_thread_id}}
    try:
        snapshot = await saver.aget(config)
    except Exception as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"checkpoint 读取失败: {e}",
        ) from e
    state = (snapshot or {}).get("channel_values") or {}
    payload = state.get("material_understanding")
    if not payload:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"material_understanding 暂未就绪 (project status={project.status})",
        )
    return {"project_id": project_id, "material_understanding": payload}


class MaterialUnderstandingDecisionRequest(BaseModel):
    """``POST /material-understanding/decision`` 请求体。

    - decision: pass / revise / skip
    - feedback: revise 时必须非空;pass / skip 时忽略
    """

    decision: Literal["pass", "revise", "skip"]
    feedback: str | None = None


@router.post("/{project_id}/material-understanding/decision")
async def decide_material_understanding(
    project_id: int,
    body: MaterialUnderstandingDecisionRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """提交材料理解决策 (D-K 兄弟节点,resume_review_task)。

    revise 必须带非空 feedback;否则 LLM-0 收不到信号会原样重出。
    """
    project = await _get_project_or_404(db, project_id)
    if project.status != "awaiting_material_understanding":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project status must be awaiting_material_understanding, "
            f"got {project.status}",
        )

    feedback = (body.feedback or "").strip()
    if body.decision == "revise" and not feedback:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "revise 必须带 feedback",
        )

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
            resume_payload={
                "kind": "material_understanding",
                "decision": body.decision,
                "feedback": feedback,
            },
            slot_token=result.token,
            reviewer_id=user.id,
        )
    except Exception as e:
        await release_project_slot(project_id, result.token)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "无法入队材料理解决策任务,请稍后重试",
        ) from e

    return {"ok": True}


def _router_app_state() -> Any:
    """读取当前 FastAPI app 的 state(用于拿 checkpointer)。

    依赖在 main.py 把 ``checkpointer`` 挂到 ``app.state``;若没挂会回退到
    None,调用方各自处理。
    """
    # 延迟 import 避免循环;每个 request 由 fastapi 注入 request 本身
    # 也可以,但这里只读全局 app,够用了。
    from ..main import app

    return app.state


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
