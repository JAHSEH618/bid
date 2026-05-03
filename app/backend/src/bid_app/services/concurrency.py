"""项目并发上限(§10.7,D-P / D-T / D-Y / D-AB / D-AF / D-AN / D-AQ)。

Redis 数据结构::

    ACTIVE_SET  : SET 持当前活跃 project_id 集合,基数即占用名额
    ALIVE_KEY   : 每项目一个 TTL key,有两种 TTL 阶段:
        ├─ API try_acquire 时设 RESERVE_TTL=300s(reservation,
        │   覆盖 arq 排队/worker 重启的延迟窗口)
        └─ task 进入 heartbeat 上下文后,首次刷成 ALIVE_TTL=60s,
            之后每 HEARTBEAT_INTERVAL=20s 续租到 60s
    WAKE_LOCK   : 唤醒函数的幂等锁(SET NX EX 30)

API 调用方语义:
- ``/start`` 时 try_acquire,占成功 → 入队 ``start_workflow_task``,
  ``Project.status='extracting'``;占失败 → ``Project.status='queued'``,不入队
- ``/review`` ``/confirm-outline`` ``/retry`` 时 try_acquire,占成功 → 入队;
  占失败 → 503 + ``Retry-After``
- worker:task 入口立刻起 heartbeat 上下文(自动首次 SET 60s);task 结束
  release + wake
- worker 启动:先 ``reconcile_active_projects()`` 扫一遍清僵尸;再 wake 一次
  处理漏唤醒的 queued
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async
import sqlalchemy as sa
import structlog

from ..config import settings
from ..db import session_factory

log = structlog.get_logger()

ACTIVE_SET = "bid_app:active_projects"
ALIVE_KEY = "bid_app:project_alive:{}"
WAKE_LOCK = "bid_app:wake_in_flight"

# D-Y 双 TTL
RESERVE_TTL = 300  # API try_acquire 设的 TTL,覆盖 enqueue→worker 启动延迟
ALIVE_TTL = 60  # task heartbeat 续租用的较短 TTL,反映"现在真在跑"
HEARTBEAT_INTERVAL = 20  # heartbeat 周期,< ALIVE_TTL 的一半留容错

# ⭐ D-AR / D-AS / D-BF 中间态超时清理
STALE_CHAPTER_TIMEOUT_SECONDS = 60
# ⭐ R-20:从 15min 缩到 3min。理由:LLM 单 chapter 5K 字流式 1-3min 跑完,
# worker 健康跑章节时 partial_flush 持续更新 processing_started_at;3min 阈值
# + worker startup reconciler 双保险,容器 rebuild 后用户不再等 15min 才看到
# retry CTA。
STALE_GENERATING_TIMEOUT_SECONDS = 60 * 3  # 3 分钟
STALE_DOCX_TIMEOUT_SECONDS = 30 * 60


def _r() -> redis_async.Redis:
    return redis_async.from_url(settings.redis_url, decode_responses=True)


class AcquireResult:
    """try_acquire_project_slot 的三态返回。"""

    def __init__(self, token: str | None, reason: str) -> None:
        self.token = token
        self.reason = reason  # "ok" | "full" | "already_active" | "stale_evicted"

    @property
    def acquired(self) -> bool:
        return self.token is not None


_TRY_ACQUIRE_LUA = """
local exists = redis.call('SISMEMBER', KEYS[1], ARGV[2])
if exists == 1 then
    local alive = redis.call('EXISTS', KEYS[2])
    if alive == 1 then
        return -1
    end
    return -2
end

local members = redis.call('SMEMBERS', KEYS[1])
local alive_count = 0
for i, m in ipairs(members) do
    if redis.call('EXISTS', 'bid_app:project_alive:' .. m) == 1 then
        alive_count = alive_count + 1
    end
end

local max = tonumber(ARGV[1])
if alive_count < max then
    redis.call('SADD', KEYS[1], ARGV[2])
    redis.call('SET', KEYS[2], ARGV[3], 'EX', tonumber(ARGV[4]))
    return 1
end
return 0
"""


async def try_acquire_project_slot(project_id: int) -> AcquireResult:
    """⭐ D-AB / D-AF / D-AN / D-AQ 四态返回。

    Lua 行为(D-AQ 修正版):
    1. project_id 在 SET + ALIVE 存在 → ``"already_active"``(真在跑,拒绝)
    2. project_id 在 SET + ALIVE 不存在 → ``"stale"`` 返回 -2,Python 端做
       evict(SREM + DB 同步标 failed)然后递归调用 try_acquire
    3. 否则数 alive 成员个数;< max → SADD + SET ALIVE_KEY → ``"ok"``
    4. ≥ max → ``"full"``
    """
    return await _try_acquire_inner(project_id, _allow_evict=True)


async def _try_acquire_inner(
    project_id: int, *, _allow_evict: bool
) -> AcquireResult:
    token = uuid.uuid4().hex
    r = _r()
    try:
        ok = await r.eval(
            _TRY_ACQUIRE_LUA,
            2,
            ACTIVE_SET,
            ALIVE_KEY.format(project_id),
            settings.max_concurrent_projects,
            project_id,
            token,
            RESERVE_TTL,
        )
    finally:
        await r.aclose()

    if ok == 1:
        return AcquireResult(token, "ok")
    if ok == -1:
        return AcquireResult(None, "already_active")
    if ok == -2:
        if _allow_evict:
            log.warning("acquire_detected_stale", project_id=project_id)
            await _evict_stale_project(project_id)
            return await _try_acquire_inner(project_id, _allow_evict=False)
        log.error("acquire_stale_after_evict", project_id=project_id)
        return AcquireResult(None, "stale_evicted")
    return AcquireResult(None, "full")


async def _evict_stale_project(project_id: int) -> None:
    """⭐ D-AN:SREM + DB 同步标 failed,绑定原则。"""
    r = _r()
    try:
        await r.srem(ACTIVE_SET, project_id)
    finally:
        await r.aclose()
    async with session_factory() as s:
        await s.execute(
            sa.text(
                "UPDATE projects SET status='failed' WHERE id=:p "
                "AND status IN ('running','extracting','outlining')"
            ),
            {"p": project_id},
        )
        await s.commit()
    log.warning("evicted_stale_project", project_id=project_id)


async def ensure_project_slot(project_id: int, token: str) -> bool:
    """task 入口校验 token 仍是 ALIVE_KEY 的值。"""
    r = _r()
    try:
        current = await r.get(ALIVE_KEY.format(project_id))
        return current == token
    finally:
        await r.aclose()


_HEARTBEAT_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
    return 1
end
return 0
"""


async def heartbeat_project(project_id: int, token: str) -> bool:
    """task 续租 ALIVE_TTL(60s),Lua CAS 保证仅持有 token 才续租。

    返回 True=续租成功;False=token 已失效(reconcile 清过 / 别人重 acquire)。
    """
    r = _r()
    try:
        ok = await r.eval(
            _HEARTBEAT_LUA, 1, ALIVE_KEY.format(project_id), token, ALIVE_TTL
        )
        return ok == 1
    finally:
        await r.aclose()


_RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
    redis.call('DEL', KEYS[1])
    redis.call('SREM', KEYS[2], ARGV[2])
    return 1
end
return 0
"""


async def release_project_slot(
    project_id: int, token: str | None = None
) -> None:
    """SREM + DEL alive。允许重复调用。

    带 token 用 Lua CAS 仅当持有 token 才释放,防止误释放别人重 acquire 的 slot。
    """
    if token is None:
        r = _r()
        try:
            async with r.pipeline(transaction=True) as p:
                p.srem(ACTIVE_SET, project_id)
                p.delete(ALIVE_KEY.format(project_id))
                await p.execute()
        finally:
            await r.aclose()
        return

    r = _r()
    try:
        await r.eval(
            _RELEASE_LUA,
            2,
            ALIVE_KEY.format(project_id),
            ACTIVE_SET,
            token,
            project_id,
        )
    finally:
        await r.aclose()


async def reconcile_active_projects() -> list[int]:
    """worker 启动调:active set 里 alive key 已不存在 → 视为僵尸,从 set 移除。"""
    r = _r()
    try:
        members = await r.smembers(ACTIVE_SET)
        if not members:
            return []
        async with r.pipeline(transaction=False) as p:
            for pid in members:
                p.exists(ALIVE_KEY.format(pid))
            results = await p.execute()
        zombies = [int(pid) for pid, alive in zip(members, results) if not alive]
        if zombies:
            await r.srem(ACTIVE_SET, *zombies)
            log.warning("reconciled_zombie_projects", project_ids=zombies)
        return zombies
    finally:
        await r.aclose()


async def get_alive_project_ids() -> list[int]:
    """⭐ D-BB:返回当前真"alive"(SET 成员 + ALIVE_KEY 仍存在)的 project ids。"""
    r = _r()
    try:
        members = await r.smembers(ACTIVE_SET)
        if not members:
            return []
        async with r.pipeline(transaction=False) as p:
            for pid in members:
                p.exists(ALIVE_KEY.format(pid))
            results = await p.execute()
        return [int(pid) for pid, alive in zip(members, results) if alive]
    finally:
        await r.aclose()


async def reconcile_periodic(ctx: dict[str, Any]) -> None:
    """arq cron(每分钟)。catch worker 不重启但 heartbeat 异常的情况(D-AG)。"""
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


async def cleanup_stale_chapters(ctx: dict[str, Any]) -> None:
    """arq cron(每分钟):回滚 API commit 后进程崩溃导致卡 reviewing/retrying/
    pending/generating 的章节(D-AR / D-AS / D-BF / D-BL / D-BS / D-BB)。"""
    active_ids = await get_alive_project_ids()
    rows: list[Any] = []
    gen_project_ids: list[int] = []

    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                f"""
                WITH stale AS (
                    SELECT c.id, c.status, c.run_id, r.project_id FROM chapters c
                    JOIN runs r ON r.id = c.run_id
                    WHERE r.project_id <> ALL(CAST(:active_ids AS int[]))
                      AND (
                        (c.status IN ('reviewing','retrying')
                         AND c.processing_started_at IS NOT NULL
                         AND c.processing_started_at <
                             NOW() - INTERVAL '{STALE_CHAPTER_TIMEOUT_SECONDS} seconds')
                        OR
                        (c.status = 'pending'
                         AND c.processing_started_at IS NOT NULL
                         AND c.processing_started_at <
                             NOW() - INTERVAL '{STALE_CHAPTER_TIMEOUT_SECONDS} seconds')
                        OR
                        (c.status = 'generating'
                         AND c.processing_started_at IS NOT NULL
                         AND c.processing_started_at <
                             NOW() - INTERVAL '{STALE_GENERATING_TIMEOUT_SECONDS} seconds')
                      )
                )
                UPDATE chapters c SET
                    status = CASE
                        WHEN s.status='reviewing'  THEN 'awaiting_review'
                        WHEN s.status='retrying'   THEN 'failed'
                        WHEN s.status='pending'    THEN 'failed'
                        WHEN s.status='generating' THEN 'failed'
                    END,
                    processing_started_at = NULL,
                    last_error = COALESCE(c.last_error, '') ||
                        ' [auto-rollback from ' || s.status || ' at ' || NOW()::text || ']'
                FROM stale s
                WHERE c.id = s.id
                RETURNING c.id, s.status AS old_status, c.status AS new_status,
                          s.project_id AS project_id
                """
            ),
            {"active_ids": active_ids},
        )
        rows = result.fetchall()

        # ⭐ D-BL + D-BS:被回滚到 failed 的章节(generating / pending),把 Project
        # 切 awaiting_review,让用户能从 P5 看到 failed 章节并 /retry
        gen_project_ids = sorted(
            {
                r.project_id
                for r in rows
                if r.old_status in ("generating", "pending")
            }
        )
        if gen_project_ids:
            await s.execute(
                sa.text(
                    "UPDATE projects SET status='awaiting_review' "
                    "WHERE id = ANY(:ids) "
                    "AND status IN ('running','extracting','outlining','failed')"
                ),
                {"ids": gen_project_ids},
            )
        await s.commit()
    if rows:
        log.warning(
            "cleanup_stale_chapters",
            count=len(rows),
            skipped_active=len(active_ids),
            rolled_back=[(r.id, r.old_status, r.new_status) for r in rows],
            project_state_restored=gen_project_ids,
        )


async def cleanup_stale_docx_jobs(ctx: dict[str, Any]) -> None:
    """arq cron(每 5 分钟,D-AS / D-AY / D-BH / D-BQ / D-BY)。

    ⚠️ D-BH:用 ``updated_at`` 判超时而不是 ``created_at``。
    ⚠️ D-BY:``finalizing`` + 文件已存在 → repair 成 done(产物完整);
            其余 in-flight 超时 → failed。
    """
    repair_done_count = 0
    async with session_factory() as s:
        finalizing = (
            await s.execute(
                sa.text(
                    "SELECT dj.id, dj.project_id, p.dir_path "
                    "FROM docx_jobs dj JOIN projects p ON p.id = dj.project_id "
                    "WHERE dj.status='finalizing'"
                )
            )
        ).mappings().all()
        for row in finalizing:
            file_path = Path(row["dir_path"]) / "proposal.docx"
            if file_path.exists():
                upd = await s.execute(
                    sa.text(
                        "UPDATE docx_jobs SET status='done', "
                        "output_path=:p, finished_at=NOW(), updated_at=NOW() "
                        "WHERE id=:i AND status='finalizing' RETURNING id"
                    ),
                    {"i": row["id"], "p": str(file_path)},
                )
                if upd.first() is not None:
                    repair_done_count += 1
                    log.info(
                        "docx_finalizing_repaired_to_done",
                        docx_job_id=row["id"],
                        project_id=row["project_id"],
                    )
        if repair_done_count:
            await s.commit()

        result = await s.execute(
            sa.text(
                f"""
                UPDATE docx_jobs
                SET status='failed',
                    error='auto-rollback: ' || status || ' > {STALE_DOCX_TIMEOUT_SECONDS}s',
                    finished_at=NOW(),
                    updated_at=NOW()
                WHERE status IN ('pending','rendering_mermaid','pandoc','finalizing')
                  AND updated_at < NOW() - INTERVAL '{STALE_DOCX_TIMEOUT_SECONDS} seconds'
                RETURNING id, project_id, status
                """
            )
        )
        rows = result.fetchall()
        await s.commit()

    if rows or repair_done_count:
        log.warning(
            "cleanup_stale_docx_jobs",
            failed=len(rows),
            repaired_to_done=repair_done_count,
            jobs=[(r.id, r.project_id, r.status) for r in rows],
        )


async def wake_queued_projects(arq_pool: Any) -> int:
    """幂等地把 status='queued' 的项目按 FIFO 入队。

    ⚠️ D-AP:不再用 SCARD 判容量(僵尸成员会算进 SCARD)。
    ⚠️ D-AX:already_active / stale_evicted / run missing 时立即标 failed,
            防 SKIP LOCKED 死循环。
    """
    woke_count = 0
    r = _r()
    try:
        got = await r.set(WAKE_LOCK, "1", nx=True, ex=30)
        if not got:
            return 0
        try:
            async with session_factory() as s:
                while True:
                    async with s.begin():
                        row = await s.execute(
                            sa.text(
                                "SELECT id FROM projects WHERE status='queued' "
                                "ORDER BY created_at "
                                "LIMIT 1 FOR UPDATE SKIP LOCKED"
                            )
                        )
                        next_pid = row.scalar_one_or_none()
                        if next_pid is None:
                            return woke_count
                        result = await try_acquire_project_slot(next_pid)
                        if not result.acquired:
                            if result.reason == "full":
                                return woke_count
                            log.error(
                                "wake_acquire_anomaly_marking_failed",
                                project_id=next_pid,
                                reason=result.reason,
                            )
                            await s.execute(
                                sa.text(
                                    "UPDATE projects SET status='failed' "
                                    "WHERE id=:p"
                                ),
                                {"p": next_pid},
                            )
                            continue
                        slot_token = result.token
                        run_row = await s.execute(
                            sa.text(
                                "SELECT id, langgraph_thread_id FROM runs "
                                "WHERE project_id=:p ORDER BY started_at DESC "
                                "LIMIT 1"
                            ),
                            {"p": next_pid},
                        )
                        run = run_row.one_or_none()
                        if run is None:
                            log.error(
                                "wake_run_missing_marking_failed",
                                project_id=next_pid,
                            )
                            await release_project_slot(next_pid, slot_token)
                            await s.execute(
                                sa.text(
                                    "UPDATE projects SET status='failed' "
                                    "WHERE id=:p"
                                ),
                                {"p": next_pid},
                            )
                            continue
                        run_id, thread_id = run
                        await s.execute(
                            sa.text(
                                "UPDATE projects SET status='extracting' "
                                "WHERE id=:p"
                            ),
                            {"p": next_pid},
                        )
                    try:
                        await arq_pool.enqueue_job(
                            "start_workflow_task",
                            project_id=next_pid,
                            run_id=run_id,
                            thread_id=thread_id,
                            slot_token=slot_token,
                        )
                        woke_count += 1
                    except Exception:
                        log.exception("wake_enqueue_failed", project_id=next_pid)
                        await release_project_slot(next_pid, slot_token)
                        async with session_factory() as s2:
                            await s2.execute(
                                sa.text(
                                    "UPDATE projects SET status='queued' "
                                    "WHERE id=:p"
                                ),
                                {"p": next_pid},
                            )
                            await s2.commit()
                        return woke_count
        finally:
            await r.delete(WAKE_LOCK)
    finally:
        await r.aclose()


class SlotLost(Exception):
    """heartbeat 续租失败 = token 已被回收。task 应当中止(D-AG)。"""


@contextlib.asynccontextmanager
async def project_heartbeat(project_id: int, token: str):
    """task 运行时上下文,每 ``HEARTBEAT_INTERVAL`` 秒续租 alive TTL(D-AM)。

    续租失败(token 不再匹配)→ 设 ``lost_event``,主循环用
    ``ensure_project_slot`` 检测后 ``raise SlotLost``。
    """
    lost_event = asyncio.Event()

    async def _loop() -> None:
        while not lost_event.is_set():
            try:
                ok = await heartbeat_project(project_id, token)
                if not ok:
                    log.warning(
                        "slot_lost_during_heartbeat",
                        project_id=project_id,
                        token_prefix=token[:8],
                    )
                    lost_event.set()
                    break
            except Exception:
                log.exception("heartbeat_failed", project_id=project_id)
            try:
                await asyncio.wait_for(
                    lost_event.wait(), timeout=HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                pass

    hb_task = asyncio.create_task(_loop())
    try:
        yield lost_event
    finally:
        if not lost_event.is_set():
            lost_event.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await hb_task
