"""SSE 端点(§12.3 / FR-3)。

GET ``/api/projects/{project_id}/stream``:订阅 ``events.bus`` 频道,把章节
token / chapter_started / chapter_failed / awaiting_review / proposal_ready
等事件推给浏览器。

心跳::

    : ping\\n\\n

每 ``PING_INTERVAL`` 秒发一次,代理(nginx / Cloudflare)默认 30-60s 静默
关闭连接,20s 心跳给容错。

⚠️ 鉴权:走 ``Depends(get_current_user)``,M1 阶段 stub 解析
``$BID_APP_DEV_USER_ID``;M2 接 JWT cookie 后透明替换。
"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated, AsyncIterator

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_current_user, get_db
from ..events.bus import event_bus
from ..models import User

router = APIRouter(prefix="/api/projects", tags=["stream"])

log = structlog.get_logger()

PING_INTERVAL = 20  # 秒。代理默认静默 30-60s 关连接,20s 心跳留容错


async def _project_visible_to_user(
    db: AsyncSession, project_id: int, user: User
) -> bool:
    """团队共享池:任何 active user 都可订阅(M1 简化版)。

    M2 收紧:仅 ``role='admin'`` 或 ``Project.created_by == user.id`` 才可订阅。
    """
    row = await db.execute(
        sa.text("SELECT 1 FROM projects WHERE id=:p"), {"p": project_id}
    )
    return row.scalar_one_or_none() is not None


@router.get("/{project_id}/stream")
async def stream(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not await _project_visible_to_user(db, project_id, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    async def gen() -> AsyncIterator[str]:
        # 立即推 ready,告诉前端订阅成功
        yield "event: ready\ndata: {}\n\n"

        async with event_bus.subscribe(project_id) as events:
            ev_iter = events.__aiter__()
            while True:
                try:
                    ev = await asyncio.wait_for(
                        ev_iter.__anext__(), timeout=PING_INTERVAL
                    )
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    # 客户端断开,gen 被 cancel — 正常退出,subscribe 上下文会清理
                    raise
                except Exception:
                    log.exception(
                        "sse_iteration_error",
                        project_id=project_id,
                        user_id=user.id,
                    )
                    break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
