"""共享 LangGraph PostgreSQL checkpointer 构造逻辑(PR-M8-1 补丁)。

历史:``worker/lifecycle.py`` 在 arq worker 启动时构造 ``AsyncPostgresSaver``,
但 FastAPI web 进程没建过自己的 saver。``api/projects.py`` 中
``GET /material-understanding`` 直接读 ``app.state.checkpointer``,因此该
端点永远返回 503 ``checkpointer 未初始化``。

把 pool + saver 的构造抽到这里,worker / web 都可以从此处建一个长生命周期
的 (pool, saver) 对。两个进程独立持有各自的连接池,不共享。

⚠️ langgraph-checkpoint-postgres 2.0.25 实测(REVIEW-1 🟡 #3):
- ``AsyncPostgresSaver.from_conn_string`` 是 ``@classmethod
  @asynccontextmanager``,入参连接随 context manager 退出而关闭——不适合
  长生命周期。
- 正确写法:``AsyncConnectionPool`` 持有连接池,直接 ``AsyncPostgresSaver(pool)``,
  调用方在 shutdown 时 close pool。
"""
from __future__ import annotations

from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..config import settings


async def open_checkpointer(
    *, max_size: int = 4
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool[Any]]:
    """开 PG 池 + 包 AsyncPostgresSaver。

    返回 ``(saver, pool)``;调用方在 shutdown 时负责 ``await pool.close()``。
    web 进程读用 ``max_size=4`` 默认就够;worker 跑 LangGraph stream 需要
    更大池,启动时传 ``max_size=10``。
    """
    pool = AsyncConnectionPool(
        conninfo=settings.langgraph_dsn,
        min_size=1,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(
        conn=cast(AsyncConnectionPool[AsyncConnection[dict[str, Any]]], pool)
    )
    await saver.setup()
    return saver, pool
