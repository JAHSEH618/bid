"""arq worker 生命周期(§17.2)。

- ``on_startup``:用 ``AsyncConnectionPool`` 起 LangGraph PG checkpointer,
  ``setup()`` 建表,放进 ctx;同时把 arq 自带的 redis 连接复用为 ``arq_pool``。
- ``on_shutdown``:close 连接池(关掉 checkpointer 持有的 PG 连接)。
- ⭐ D-AG:启动时 ``reconcile_active_projects()`` 清僵尸 + 一次 wake 把
  漏唤醒的 queued 项目入队。

⚠️ ``open_checkpointer`` 共享逻辑见 ``workflow/checkpointer.py``;web 进程
也用同一个 helper,两侧各自持有独立的连接池(不共享)。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from ..db import session_factory
from ..services.concurrency import (
    reconcile_active_projects,
    wake_queued_projects,
)
from ..workflow.checkpointer import open_checkpointer

log = structlog.get_logger()


async def _reconcile_orphaned_chapters_on_startup() -> None:
    """⭐ R-20:worker 启动立即清理 zombie 章节(processing_started_at 非 NULL
    + status IN generating/reviewing/retrying)。

    场景:容器 rebuild/restart 后,arq worker 进程是新的,之前 in-flight 的
    章节 task 已死。它们 status 仍是 generating 但永远不会有 worker 跑它。
    本函数把所有这种章节标 failed + last_error 写明,前端能看到 retry CTA,
    不必等 cron stale 阈值(R-20 同步缩到 180s)。

    保守路径:只 reconcile ``processing_started_at < NOW() - INTERVAL '30 seconds'``
    的章节,给真有快速续跑的 worker 留 30 秒 grace,避免误伤刚 enqueue 不久的
    task。后续如出现 active_set/heartbeat 残留,周期 cron 仍会兜底。

    错误吞掉(打 log 不阻塞 worker 启动)。
    """
    grace = 30  # 秒
    try:
        async with session_factory() as s:
            result = await s.execute(
                sa.text(
                    f"""
                    UPDATE chapters c SET
                        status = CASE
                            WHEN c.status='reviewing'  THEN 'awaiting_review'
                            WHEN c.status='retrying'   THEN 'failed'
                            WHEN c.status='generating' THEN 'failed'
                        END,
                        processing_started_at = NULL,
                        last_error = COALESCE(c.last_error, '') ||
                            ' [worker 启动时自动回滚:容器 restart 切断 in-flight workflow]'
                    WHERE c.status IN ('generating','reviewing','retrying')
                      AND c.processing_started_at IS NOT NULL
                      AND c.processing_started_at < NOW() - INTERVAL '{grace} seconds'
                    RETURNING c.id, c.run_id, c.index, c.status
                    """
                )
            )
            rows = result.all()
            if rows:
                log.warning(
                    "worker_startup_reconciled_orphans", count=len(rows)
                )
            # ⭐ 把对应 run + project 状态从 failed 改回 running:之前若被 cron
            # cleanup 抢标 failed,但实际 chapters 已被本函数回滚为
            # failed/awaiting_review,UI 上需 running 状态才让用户能 retry 续跑。
            await s.execute(
                sa.text(
                    """
                    UPDATE runs SET status='running', finished_at=NULL
                    WHERE status='failed' AND id IN (
                      SELECT DISTINCT run_id FROM chapters WHERE status='failed'
                      AND last_error LIKE '%worker 启动时自动回滚%'
                    )
                    """
                )
            )
            await s.execute(
                sa.text(
                    """
                    UPDATE projects SET status='running'
                    WHERE status='failed' AND id IN (
                      SELECT project_id FROM runs WHERE status='running'
                    )
                    """
                )
            )
            await s.commit()
    except Exception:
        log.exception("worker_startup_reconcile_orphans_failed")


async def on_startup(ctx: dict[str, Any]) -> None:
    # worker 并发跑 LangGraph stream,池要更大;web 默认 4 不够。
    saver, pool = await open_checkpointer(max_size=10)

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

    # ⭐ R-20:清理上次容器 restart 切断的 in-flight 章节(generating/reviewing/
    # retrying)。错误吞掉,不阻塞 worker 启动。
    await _reconcile_orphaned_chapters_on_startup()


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """关连接池(saver 没有自己的 close,持有 pool 才有真句柄)。"""
    pool = ctx.get("checkpointer_pool")
    if pool is not None:
        try:
            await pool.close()
        except Exception:
            log.exception("worker_shutdown_checkpointer_pool_close_failed")
