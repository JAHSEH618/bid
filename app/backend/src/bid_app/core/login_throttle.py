"""登录失败锁(FR-6.7 / D-Q,§14.3.2)。

同一 IP 每分钟登录失败 ≥ ``settings.login_fail_max_per_minute`` 次 → 锁该 IP
``settings.login_lock_seconds`` 秒。登录成功清零失败计数(锁不动,等过期)。
"""
from __future__ import annotations

import redis.asyncio as redis_async

from ..config import settings

_FAIL_KEY = "bid_app:login_fail:{ip}"
_LOCK_KEY = "bid_app:login_lock:{ip}"


def _r() -> redis_async.Redis:
    return redis_async.from_url(settings.redis_url, decode_responses=True)


async def is_locked(ip: str) -> bool:
    r = _r()
    try:
        return (await r.get(_LOCK_KEY.format(ip=ip))) is not None
    finally:
        await r.aclose()


async def record_fail(ip: str) -> bool:
    """记录一次失败。返回 True 表示这次失败之后该 IP 已被锁。"""
    r = _r()
    try:
        n = await r.incr(_FAIL_KEY.format(ip=ip))
        if n == 1:
            await r.expire(_FAIL_KEY.format(ip=ip), 60)  # 1 分钟窗口
        if n >= settings.login_fail_max_per_minute:
            await r.set(
                _LOCK_KEY.format(ip=ip),
                "1",
                ex=settings.login_lock_seconds,
            )
            return True
        return False
    finally:
        await r.aclose()


async def clear_fails(ip: str) -> None:
    """登录成功后清零失败计数(锁还在的话不动,等 TTL 过期)。"""
    r = _r()
    try:
        await r.delete(_FAIL_KEY.format(ip=ip))
    finally:
        await r.aclose()
