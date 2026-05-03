"""arq worker 生命周期(§17.2)。

- ``on_startup``:用 ``AsyncConnectionPool`` 起 LangGraph PG checkpointer,
  ``setup()`` 建表,放进 ctx;同时把 arq 自带的 redis 连接复用为 ``arq_pool``。
- ``on_shutdown``:close 连接池(关掉 checkpointer 持有的 PG 连接)。
- ⭐ D-AG:启动时 ``reconcile_active_projects()`` 清僵尸 + 一次 wake 把
  漏唤醒的 queued 项目入队。

⚠️ langgraph-checkpoint-postgres 2.0.25 实测(REVIEW-1 🟡 #3 修复):
``AsyncPostgresSaver.from_conn_string`` 是 ``@classmethod @asynccontextmanager``,
入参连接随 context manager 退出而关闭——不适合 worker 长生命周期。
spec §17.2 写法 ``saver = AsyncPostgresSaver.from_conn_string(...) ; await
saver.setup()`` 在该版本上拿到的是 ``_AsyncGeneratorContextManager`` 对象
而不是 saver 实例,且没有 ``async with`` 包裹连接已关。

正确写法:用 ``AsyncConnectionPool`` + 直接 ``AsyncPostgresSaver(pool)``
构造,池由 worker 持有到 shutdown(``__init__`` 入参 ``conn:
AsyncConnection | AsyncConnectionPool``,见 ``_ainternal.Conn`` 联合类型)。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..config import settings
from ..db import session_factory
from ..services.concurrency import (
    reconcile_active_projects,
    wake_queued_projects,
)

log = structlog.get_logger()


async def on_startup(ctx: dict[str, Any]) -> None:
    # AsyncConnectionPool:LangGraph 文档与 _ainternal.Conn 联合类型都允许走池
    pool = AsyncConnectionPool(
        conninfo=settings.langgraph_dsn,
        min_size=1,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,  # 显式 await pool.open() 控制启动语义
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(conn=pool)
    await saver.setup()

    ctx["checkpointer"] = saver
    ctx["checkpointer_pool"] = pool  # on_shutdown 用
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
    """关连接池(saver 没有自己的 close,持有 pool 才有真句柄)。"""
    pool = ctx.get("checkpointer_pool")
    if pool is not None:
        try:
            await pool.close()
        except Exception:
            log.exception("worker_shutdown_checkpointer_pool_close_failed")
