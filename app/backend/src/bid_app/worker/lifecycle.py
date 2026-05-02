"""arq worker 生命周期(§17.2)。

- ``on_startup``:实例化 ``AsyncPostgresSaver``(LangGraph PG checkpointer),
  setup() 建表,放进 ctx;同时把 arq 自带的 redis 连接复用为 ``arq_pool``。
- ``on_shutdown``:close checkpointer。
- ⭐ D-AG:启动时 ``reconcile_active_projects()`` 清僵尸 + 一次 wake 把
  漏唤醒的 queued 项目入队。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ..config import settings
from ..db import session_factory
from ..services.concurrency import (
    reconcile_active_projects,
    wake_queued_projects,
)

log = structlog.get_logger()


async def on_startup(ctx: dict[str, Any]) -> None:
    saver = AsyncPostgresSaver.from_conn_string(settings.langgraph_dsn)
    await saver.setup()
    ctx["checkpointer"] = saver
    ctx["arq_pool"] = ctx["redis"]  # arq 把 redis 连接放在 ctx['redis']

    zombies = await reconcile_active_projects()
    if zombies:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE projects SET status='failed' "
                    "WHERE id = ANY(:ids) "
                    "AND status IN ('running','extracting','outlining')"
                ),
                {"ids": zombies},
            )
            await s.commit()
        log.warning("worker_startup_marked_zombies_failed", zombies=zombies)

    woke = await wake_queued_projects(ctx["arq_pool"])
    if woke:
        log.info("worker_startup_woke_queued", count=woke)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    saver = ctx.get("checkpointer")
    if saver is not None:
        try:
            await saver.close()
        except Exception:
            log.exception("worker_shutdown_checkpointer_close_failed")
