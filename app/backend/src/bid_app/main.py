"""FastAPI app 装配(§15)。

M0 骨架 → M1 增量挂:health(§15.4)/ stream(§12.3)/ projects / chapters
路由,以及 lifespan 内 redis pool。
M2 / M3 继续挂 auth / me / admin / docx router 与中间件。

⚠️ ``app.state.redis``(异步 redis client)在 lifespan 启动时实例化,
``api/health.py`` 用 ``request.app.state.redis.ping()`` 检测连通。
"""
from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

import redis.asyncio as redis_async
from fastapi import FastAPI

from bid_app.config import settings


def _print_startup_banner() -> None:
    """M5-4 启动横幅:在 uvicorn 起来后立即 print 到 stdout。

    内容:
      - 默认 admin/admin123 提示(R10 + 防忘改密)
      - BID_APP_MASTER_KEY sha256 前缀(R10:运维与备份比对确认)

    所有 print 强制 flush=True 走 stdout(supervisord 已配 stdout_logfile=/dev/fd/1,
    docker compose logs 能看到)。
    """
    bar = "=" * 64
    mk_hash = sha256(settings.bid_app_master_key.encode()).hexdigest()[:16]

    print(bar, flush=True, file=sys.stdout)
    print(f"  bid-app 启动完成(端口 {settings.app_port})", flush=True, file=sys.stdout)
    print(
        f"  ⚠️  默认账号 {settings.admin_default_username} / "
        f"{settings.admin_default_password} —— 首次登录会强制改密",
        flush=True,
        file=sys.stdout,
    )
    print(
        f"  🔐 BID_APP_MASTER_KEY sha256:{mk_hash}...",
        flush=True,
        file=sys.stdout,
    )
    print(
        "      (R10:此 key 一旦丢失,所有 ApiKey 永久不可解密。请与备份比对此哈希)",
        flush=True,
        file=sys.stdout,
    )
    print(bar, flush=True, file=sys.stdout)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    顺序:
      1. 启动横幅(M5-4)— 必须在最前
      2. redis async client 实例化挂 ``app.state.redis``(/health 用,M1)
      3. (M1+) LangGraph AsyncPostgresSaver setup
      4. (M2/M3) 继续 append
    """
    _print_startup_banner()

    redis_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client

    # M1+ 在此 append:
    # await checkpointer.setup()
    # ...

    try:
        yield
    finally:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        # M1+ 在此 append shutdown:
        # await checkpointer.close()


app = FastAPI(
    title="bid-app",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# === 路由挂载(M1) ===
# 顺序无关紧要,但保持业务 API 集中,health 在最前。
from bid_app.api import health as _health_router  # noqa: E402
from bid_app.api import stream as _stream_router  # noqa: E402

app.include_router(_health_router.router)
app.include_router(_stream_router.router)
# M1-7 / M1-9:projects / chapters router 在 #14 / #13 挂
# M2 / M3:auth / me / admin / docx router 后续挂
