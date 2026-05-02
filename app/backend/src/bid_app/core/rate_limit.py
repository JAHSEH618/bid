"""全局限流(§14.3.1 / NFR-4)。

slowapi 全局每 IP 100 req/min(默认从 ``settings.global_rate_limit`` 取)。
``main.py`` 注册 ``app.state.limiter = limiter`` + 加 ``SlowAPIMiddleware``,
中间件方式让 ``default_limits`` 自动应用,无需每个端点 ``@limiter.limit``。

登录失败锁(FR-6.7)是另一层,见 ``core/login_throttle.py``(M2-2)。
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from ..config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    default_limits=[settings.global_rate_limit],
)
