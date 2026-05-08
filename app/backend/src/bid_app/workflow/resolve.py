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
from ..models import Project

log = structlog.get_logger()


@dataclass
class ResolvedModels:
    """三类任务的最终生效模型名(LiteLLM 格式)。"""
    outline_model: str
    chapter_model: str
    visuals_model: str


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
