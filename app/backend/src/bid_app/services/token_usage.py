"""Token 计费服务(§8 token_usage 表)。

由 ``services/llm.py`` 在每次 LLM 调用 finally 末尾 await。
DB 写入失败仅 log 不传播(token 记账是观测,不该阻塞业务路径)。
"""
from __future__ import annotations

import structlog

from ..db import session_factory
from ..models import TokenUsage

log = structlog.get_logger()


async def record_token_usage(
    *,
    user_id: int | str,
    project_id: int,
    run_id: int | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """记账一次 LLM 调用的 token 消费。"""
    try:
        uid = int(user_id) if not isinstance(user_id, int) else user_id
    except (TypeError, ValueError):
        log.warning("token_usage_invalid_user_id", user_id=user_id)
        return

    if uid <= 0:
        # CLI run_local 走 user_id=0 / project_id=-1 这种 fake 标识,跳过 DB
        log.info(
            "token_usage_skipped_fake_id",
            user_id=uid,
            project_id=project_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return

    try:
        async with session_factory() as s, s.begin():
            s.add(
                TokenUsage(
                    user_id=uid,
                    project_id=project_id if project_id > 0 else None,
                    run_id=run_id,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            )
    except Exception:
        log.exception(
            "token_usage_db_write_failed",
            user_id=uid,
            project_id=project_id,
            model=model,
        )
