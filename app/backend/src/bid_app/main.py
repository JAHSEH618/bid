"""FastAPI app 骨架。完整路由 / middleware 在 M1-M3 增量挂载。

本文件 M0 时只暴露:
  - /health(简易版,后续 §15.4 改写为查 db + redis)
  - lifespan 启动横幅(M5-4):打印默认 admin 警告 + master_key sha256 前缀

后续:
  - M1 注册 stream / projects / chapters / health(完整) router;
        lifespan 内 LangGraph AsyncPostgresSaver setup(§17.2)
  - M2 注册 auth / me / admin router、SecurityHeaders / SlowAPI middleware
  - M3 注册 docx router
  - 末尾 SPA fallback(§15.6)
"""
from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

from fastapi import FastAPI
from fastapi.responses import JSONResponse

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

    M0:仅启动横幅(M5-4)。
    M1:在 yield 之前 append LangGraph checkpointer setup / db pool 初始化等。
    M2/M3:可继续在 yield 前 / yield 后 append shutdown 逻辑(关 redis / db pool)。
    后续扩展时,**保持启动横幅在最前**。
    """
    _print_startup_banner()
    # M1+ 在此 append:
    # await checkpointer.setup()
    # ...
    yield
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


@app.get("/health", include_in_schema=False)
async def health_skeleton() -> JSONResponse:
    """M0 占位 health。M1 §15.4 用 db + redis 真实查询版本覆盖。"""
    return JSONResponse({"app": "ok", "port": settings.app_port})
