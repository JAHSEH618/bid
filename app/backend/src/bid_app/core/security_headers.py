"""安全头中间件(§14.4)。

X-Content-Type-Options: nosniff、X-Frame-Options: DENY、Referrer-Policy:
no-referrer、CSP(内网 SPA 友好版,允许 inline style 给 react-markdown
/ mermaid 自渲)。

⚠️ 用 setdefault 而不是 set,允许 health/specific 端点(如果将来需要)
覆盖。frame-ancestors 'none' 等同 X-Frame-Options: DENY 的双保险。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        return response
