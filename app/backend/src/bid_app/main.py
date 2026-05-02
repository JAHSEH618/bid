"""FastAPI app 骨架。完整路由 / middleware 在 M1-M3 增量挂载。

本文件 M0 时只暴露:
  - /health(简易版,后续 §15.4 改写为查 db + redis)

后续:
  - M1 注册 stream / projects / chapters / health(完整) router
  - M2 注册 auth / me / admin router、SecurityHeaders / SlowAPI middleware
  - M3 注册 docx router
  - 末尾 SPA fallback(§15.6)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bid_app.config import settings

app = FastAPI(
    title="bid-app",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.get("/health", include_in_schema=False)
async def health_skeleton() -> JSONResponse:
    """M0 占位 health。M1 §15.4 用 db + redis 真实查询版本覆盖。"""
    return JSONResponse({"app": "ok", "port": settings.app_port})
