"""健康检查(§15.4)。

只查 ``db + redis``,不查 LLM(D-G;LLM 检查由 ``GET /api/me/api-key/test``
端点专门承担,见 §15.5,M2-5 落地)。

返回:
- 全 ok → 200
- 任一 fail → 503

⭐ D-DQ:本模块 import ``session_factory``,测试 conftest 必须把
``session_factory`` 重定向到 test session 才能让 ``/health`` 走测试 DB。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..db import session_factory

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    checks: dict[str, str] = {"app": "ok"}
    try:
        async with session_factory() as s:
            await s.execute(sa.text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"fail: {e}"

    redis_client: Any | None = getattr(request.app.state, "redis", None)
    if redis_client is None:
        checks["redis"] = "skipped: not initialized"
    else:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"fail: {e}"

    code = 200 if all(v == "ok" or v.startswith("skipped") for v in checks.values()) else 503
    return JSONResponse(checks, status_code=code)
