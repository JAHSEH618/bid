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

⭐ 订阅范围:**团队共享池**(产品定义)。任何登录用户都可订阅任何项目的
SSE 流,理由是协作场景(团队成员共享 API key 配额、共看进度)。M2 **不**
按 admin / creator 收紧 —— 这是设计意图,不是 TODO。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

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


async def _project_visible_to_user(db: AsyncSession, project_id: int, user: User) -> bool:
    """⭐ 团队共享池设计(产品决定,REQUIREMENTS.md 写明):任何 active user
    都可订阅项目流;**M2 不收紧**。

    历史注释曾写"M2 收紧仅 admin / creator 可订阅",已废弃 — 团队成员需要
    互相看到"现在谁的 key 在跑哪个项目",这是协作前提,**不是**漏洞。
    项目本身的访问控制(创建 / 删除 / /start)走另一条路径。

    本函数仅校验 project 存在,通过 get_current_user 已确保 user 是登录态。
    """
    row = await db.execute(sa.text("SELECT 1 FROM projects WHERE id=:p"), {"p": project_id})
    return row.scalar_one_or_none() is not None


@router.get(
    "/{project_id}/stream",
    response_class=StreamingResponse,
    response_model=None,
)
async def stream(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not await _project_visible_to_user(db, project_id, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    async def gen() -> AsyncIterator[str]:
        # 先 subscribe 后再 yield ready —— 反过来会留出"ready 已发但订阅未建"
        # 的真空窗,工作流刚发布的事件会绕过这条连接。
        async with event_bus.subscribe(project_id) as events:
            yield "event: ready\ndata: {}\n\n"
            ev_iter = events.__aiter__()
            while True:
                try:
                    ev = await asyncio.wait_for(ev_iter.__anext__(), timeout=PING_INTERVAL)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except TimeoutError:
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
