"""TraceId 中间件(§19.3)。

为每个请求注入 trace_id 到 structlog contextvars。请求头有 ``X-Trace-Id``
就复用,否则随机 16 hex 字符。响应头回 ``X-Trace-Id`` 让前端 / 上游可串联。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
