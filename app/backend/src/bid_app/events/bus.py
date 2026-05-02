"""跨进程事件总线(§12.2)。

- arq worker:调 ``publish()`` 把事件 PUBLISH 到 Redis 频道
- uvicorn:订阅频道,SSE 端点把事件流给浏览器
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis_async
import structlog

from ..config import settings

log = structlog.get_logger()


def _channel(project_id: int) -> str:
    return f"bid_app:events:project:{project_id}"


class EventBus:
    def __init__(self, url: str) -> None:
        self._url = url
        self._pub: redis_async.Redis | None = None

    async def start(self) -> None:
        self._pub = redis_async.from_url(self._url, decode_responses=True)
        await self._pub.ping()

    async def stop(self) -> None:
        if self._pub is not None:
            await self._pub.aclose()
            self._pub = None

    async def publish(self, project_id: int, event: dict[str, Any]) -> None:
        if self._pub is None:
            await self.start()
        assert self._pub is not None
        await self._pub.publish(
            _channel(project_id), json.dumps(event, ensure_ascii=False)
        )

    @asynccontextmanager
    async def subscribe(
        self, project_id: int
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        client = redis_async.from_url(self._url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(_channel(project_id))

        async def gen() -> AsyncIterator[dict[str, Any]]:
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                yield json.loads(msg["data"])

        try:
            yield gen()
        finally:
            await pubsub.unsubscribe(_channel(project_id))
            await pubsub.aclose()
            await client.aclose()


event_bus = EventBus(settings.redis_url)
