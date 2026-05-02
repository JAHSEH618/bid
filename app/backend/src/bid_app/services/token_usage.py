"""Token 计费服务。

M0:只 structlog 记 line(没有 ``TokenUsage`` 模型)。
M1 (#10) 增 DB 写入(§8 token_usage 表 + ``models/token_usage.py``);
M1 (#7) `services/concurrency.py` 同时增 ``note_chapter_finalized`` 之类辅助。
保持外部签名稳定。
"""
from __future__ import annotations

import structlog

from ..db import session_factory

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
    """记账一次 LLM 调用的 token 消费。

    M0 临时实现:只 stdout 记录,**不写 DB**(M1 用 ``TokenUsage`` 模型替换)。
    """
    log.info(
        "token_usage_recorded",
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    # 防止 lint 把 session_factory 标 unused;M1 会真正写库。
    _ = session_factory
