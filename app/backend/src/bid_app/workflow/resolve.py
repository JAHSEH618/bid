"""工作流共享解析器(§0002)。

从 Project 快照读取用户配置的模型名,未配置时回退到 settings 全局默认值。
与 D-C ApiKey 快照模式一致:用户改模型不影响已在跑的项目。
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select

from ..config import settings
from ..db import session_factory
from ..models import Chapter, Project, Run

log = structlog.get_logger()


@dataclass
class ResolvedModels:
    """三类任务的最终生效模型名(LiteLLM 格式)。"""

    outline_model: str
    chapter_model: str
    visuals_model: str


async def resolve_api_key(project_id: int, run_id: int | None = None) -> str:
    """解析项目启动时快照的 ApiKey。

    生产路径严格依赖 ``Project.encrypted_api_key_snapshot``;仅 CLI / 本地路径
    允许 ``BID_APP_CLI_API_KEY`` fallback。
    """
    import os

    is_production = run_id is not None and run_id > 0

    encrypted: bytes | None = None
    try:
        from ..core.crypto import decrypt_api_key

        async with session_factory() as s:
            row = await s.execute(
                select(Project.encrypted_api_key_snapshot).where(Project.id == project_id)
            )
            encrypted = row.scalar_one_or_none()
    except Exception as e:
        if is_production:
            raise RuntimeError(f"db error resolving api_key for project {project_id}: {e}") from e

    if encrypted is not None:
        try:
            return decrypt_api_key(encrypted)
        except Exception as e:
            if is_production:
                raise RuntimeError(
                    f"decrypt api_key failed for project {project_id} "
                    f"(master_key 与启动时不一致?R10 检查): {e}"
                ) from e

    if is_production:
        raise RuntimeError(f"project {project_id} has no api_key snapshot; did /start succeed?")

    cli_key = os.environ.get("BID_APP_CLI_API_KEY")
    if cli_key:
        return cli_key
    raise RuntimeError(
        f"project {project_id} has no api_key snapshot; did /start succeed? "
        "(or set BID_APP_CLI_API_KEY for CLI mode)"
    )


async def resolve_user_id(project_id: int) -> int:
    """token_usage 记账用的用户 ID。缺失时返 0,不阻断工作流。"""
    try:
        async with session_factory() as s:
            row = await s.execute(select(Project.api_key_owner).where(Project.id == project_id))
            return row.scalar_one_or_none() or 0
    except Exception:
        return 0


async def resolve_models(project_id: int) -> ResolvedModels:
    """从 Project.xxx_model_snapshot 读取模型,未设置时回退 settings 默认值。

    设计决策:
      - 读 Project 快照而非 User 表:工作流启动后用户改自己模型不影响本项目
      - NULL → settings 默认:兼容老项目(迁移前建的项目快照为空)
      - DB 异常 → 全部回退 settings 默认并 log warning(不阻塞工作流)
    """
    outline_model = settings.llm1_outline_model
    chapter_model = settings.llm2_chapter_model
    visuals_model = settings.llm3_visuals_model

    try:
        async with session_factory() as s:
            row = await s.execute(
                select(
                    Project.outline_model_snapshot,
                    Project.chapter_model_snapshot,
                    Project.visuals_model_snapshot,
                ).where(Project.id == project_id)
            )
            result = row.one_or_none()
            if result is not None:
                s_outline, s_chapter, s_visuals = result
                if s_outline:
                    outline_model = s_outline
                if s_chapter:
                    chapter_model = s_chapter
                if s_visuals:
                    visuals_model = s_visuals
    except Exception:
        log.warning(
            "resolve_models_db_failed_fallback_to_defaults",
            project_id=project_id,
        )

    return ResolvedModels(
        outline_model=outline_model,
        chapter_model=chapter_model,
        visuals_model=visuals_model,
    )


async def resolve_chapter_model(
    project_id: int,
    run_id: int | None,
    chapter_index: int,
    chapter: dict[str, object] | None = None,
) -> str:
    """解析当前章节 LLM-2 模型。

    优先级:
      1. chapters.model_snapshot(审核页可在生成/重写前修改)
      2. state.chapters[i].chapter_model(CLI / 旧 checkpoint 兼容)
      3. projects.chapter_model_snapshot
      4. settings.llm2_chapter_model
    """
    fallback = (await resolve_models(project_id)).chapter_model

    if run_id is not None and run_id > 0:
        try:
            async with session_factory() as s:
                row = await s.execute(
                    select(Chapter.model_snapshot)
                    .join(Run, Run.id == Chapter.run_id)
                    .where(Run.id == run_id, Chapter.index == chapter_index)
                )
                selected = row.scalar_one_or_none()
                if selected:
                    return selected
        except Exception:
            log.warning(
                "resolve_chapter_model_db_failed_fallback",
                project_id=project_id,
                run_id=run_id,
                chapter_index=chapter_index,
            )

    from_state = ""
    if chapter:
        raw = chapter.get("chapter_model")
        if isinstance(raw, str):
            from_state = raw.strip()
    if from_state:
        return from_state

    return fallback
