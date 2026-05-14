"""FastAPI app 装配(§15)。

M0 骨架 → M1 增量挂:health(§15.4)/ stream(§12.3)/ projects / chapters
路由,以及 lifespan 内 redis pool。
M2 / M3 继续挂 auth / me / admin / docx router 与中间件。
M0/M1/M2/M3 router 全部挂完后末尾加 SPA fallback(§15.6)。

⚠️ ``app.state.redis``(异步 redis client)在 lifespan 启动时实例化,
``api/health.py`` 用 ``request.app.state.redis.ping()`` 检测连通。
"""
from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

import redis.asyncio as redis_async
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from bid_app.config import settings
from bid_app.core.middleware import TraceIdMiddleware
from bid_app.core.rate_limit import limiter
from bid_app.core.security_headers import SecurityHeadersMiddleware
from bid_app.workflow.checkpointer import open_checkpointer


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
      2. redis async client 挂 ``app.state.redis``(/health / login throttle)
      3. arq pool 挂 ``app.state.arq_pool``(API enqueue_job 用)
      4. LangGraph PG checkpointer 挂 ``app.state.checkpointer`` —— 让
         ``GET /material-understanding`` 等读 checkpoint 的端点能拿到 saver。
         worker 进程有自己独立的 saver/pool,两者不共享 (各自的 PG 连接池)。
    """
    _print_startup_banner()

    redis_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client

    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.arq_pool = arq_pool

    saver, saver_pool = await open_checkpointer()
    app.state.checkpointer = saver
    app.state.checkpointer_pool = saver_pool

    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await saver_pool.close()
        with contextlib.suppress(Exception):
            await arq_pool.aclose()
        with contextlib.suppress(Exception):
            await redis_client.aclose()


app = FastAPI(
    title="bid-app",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# === Middleware(M2-2,从外到内执行顺序:TraceId → SlowAPI → SecurityHeaders) ===
# Starlette 中 add_middleware **后注册的先执行**(LIFO),所以这里反向写:
# 1. SecurityHeaders 在最里(响应阶段最先 setdefault 头)
# 2. SlowAPI 在中间(请求阶段触发限流)
# 3. TraceId 在最外(请求阶段最先生成 trace_id 注入 contextvars)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(TraceIdMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# === 路由挂载(M1 + M2 + M3) ===
from bid_app.api import admin as _admin_router  # noqa: E402
from bid_app.api import auth as _auth_router  # noqa: E402
from bid_app.api import chapters as _chapters_router  # noqa: E402
from bid_app.api import docx as _docx_router  # noqa: E402
from bid_app.api import health as _health_router  # noqa: E402
from bid_app.api import me as _me_router  # noqa: E402
from bid_app.api import projects as _projects_router  # noqa: E402
from bid_app.api import stream as _stream_router  # noqa: E402

app.include_router(_health_router.router)
app.include_router(_auth_router.router)
app.include_router(_me_router.router)
app.include_router(_admin_router.router)
app.include_router(_stream_router.router)
app.include_router(_projects_router.router)
app.include_router(_chapters_router.router)
app.include_router(_docx_router.router)
# PR-M6-2:单章 Word 导出走 /api/chapters/{chapter_id}/export.docx
app.include_router(_docx_router.chapter_router)


# === SPA fallback(§15.6) ===
# **必须在所有 /api/* router 之后挂**,否则会先吞掉真 API 路径。
# 默认 /app/frontend/dist(Dockerfile COPY 目标),允许 BID_APP_STATIC_DIR
# 环境变量覆盖(本地 dev 模式可指向 host 的 frontend/dist)。
_STATIC_DIR = Path(os.environ.get("BID_APP_STATIC_DIR", "/app/frontend/dist"))


@app.get(
    "/{full_path:path}",
    include_in_schema=False,
    response_class=FileResponse,
    response_model=None,
)
async def spa_fallback(full_path: str) -> FileResponse:
    """前端 React Router 的非 /api/* 路径,统一返回 index.html。

    - /api/* 路径已被前面 router 处理,本 handler 不会拿到(FastAPI 路由
      匹配时按注册顺序,但本端点接受任意 path,所以仍要显式拦 /api 前缀
      防露底)
    - /health 也已经被 health_router 处理;本兜底显式 404 防止"/health" 被
      返成 index.html
    - 静态资源(/assets/foo.js 等)走 ``static_dir / requested`` 直接返;
      未命中文件 → fallback 到 index.html(SPA 路由模式)
    """
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="not found")

    static_dir = _STATIC_DIR
    index_html = static_dir / "index.html"
    if not index_html.is_file():
        # 静态目录未挂(开发模式直接 uvicorn,前端走 vite dev server)→
        # 让前端开发者看到清晰错误,而不是 silent 404
        raise HTTPException(
            status_code=404,
            detail=(
                f"frontend static dir not built at {static_dir}; "
                "run pnpm build or set BID_APP_STATIC_DIR"
            ),
        )

    if full_path:
        requested = static_dir / full_path
        # 防 path traversal:resolved 必须在 static_dir 内
        try:
            resolved = requested.resolve()
            resolved.relative_to(static_dir.resolve())
        except (OSError, ValueError):
            return FileResponse(index_html)
        if resolved.is_file():
            return FileResponse(resolved)

    return FileResponse(index_html)
