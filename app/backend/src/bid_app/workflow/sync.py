"""state ↔ DB 同步钩子(§10.6 + §10.6d + FR-4.7)。

把 LangGraph WorkflowState 的变化同步到 DB Chapter / Project / ChapterVersion
表 + 发 SSE 事件。LangGraph state 是 in-memory + checkpoint 持久化,但前端
展示用的是 DB,两层必须保持一致。

API:
- ``sync_chapter_to_db(run_id, index, **fields)`` — D-BP 白名单 UPDATE
- ``sync_project_status(project_id, status)`` — UPDATE projects
- ``sync_outline_to_db(run_id, chapters, *, replace=False)`` — upsert chapters
- ``save_chapter_version(run_id, index, body, *, feedback_in)`` — append
  ChapterVersion(自动取下一个 version 号)
- ``mark_chapter_versions_abandoned(chapter_id)`` — FR-4.7 retry 时把当前
  未审版本标 abandoned=true
- ``record_review_event(chapter_id, reviewer_id, decision, feedback_text)`` —
  写 ReviewEvent
- ``publish_event(project_id, type_, **payload)`` — SSE 包装,失败不传播
"""
from __future__ import annotations

from typing import Any, cast

import sqlalchemy as sa
import structlog
from sqlalchemy import CursorResult
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_factory
from ..events.bus import event_bus

log = structlog.get_logger()


# ⭐ D-BP:章节级 sync 字段白名单。新增字段时往这里加;非白名单字段直接 raise
# 而不是静默忽略,因为后者会让"上游写错列名"在测试中沉默,生产又看不到。
_CHAPTER_SYNC_ALLOWED = frozenset(
    {
        "status",
        "final_text",
        "last_error",
        "retry_count",
        "processing_started_at",  # D-AR / D-BF
    }
)


def _build_update_sql(fields: dict[str, Any]) -> str:
    """根据 fields 生成
    ``UPDATE chapters SET k=:k, ... WHERE run_id=:r AND index=:i``。

    白名单限制 + 字典 key 必须是 Python 标识符(防异常字符)。
    """
    bad = [
        k
        for k in fields
        if k not in _CHAPTER_SYNC_ALLOWED or not k.isidentifier()
    ]
    if bad:
        raise ValueError(f"sync_chapter_to_db: disallowed fields: {bad}")
    set_clause = ", ".join(f"{k}=:{k}" for k in fields)
    return f"UPDATE chapters SET {set_clause} WHERE run_id=:r AND index=:i"


async def sync_chapter_to_db(run_id: int, index: int, **fields: Any) -> None:
    """把 chapter 字段更新到 DB(D-BP 白名单守护)。"""
    if not fields:
        return
    sql = _build_update_sql(fields)
    async with session_factory() as s:
        await s.execute(sa.text(sql), {"r": run_id, "i": index, **fields})
        await s.commit()


async def sync_project_status(project_id: int, status: str) -> None:
    async with session_factory() as s:
        await s.execute(
            sa.text("UPDATE projects SET status=:s WHERE id=:p"),
            {"s": status, "p": project_id},
        )
        await s.commit()


async def sync_outline_to_db(
    run_id: int,
    chapters: list[dict[str, Any]],
    *,
    replace: bool = False,
) -> None:
    """把 chapters 数组落到 chapters 表。``replace=True`` 时先清空再写。

    用 ORM insert 而不是裸 sa.text + JSON 字符串绑定,SQLAlchemy 自己会把
    list 序列化成 jsonb,避免 sa.text 时的类型推断歧义。
    """
    # 延迟 import,M1 落 models 之后才有
    from ..models import Chapter

    async with session_factory() as s, s.begin():
        default_model_row = await s.execute(
            sa.text(
                "SELECT p.chapter_model_snapshot "
                "FROM runs r JOIN projects p ON p.id = r.project_id "
                "WHERE r.id=:r"
            ),
            {"r": run_id},
        )
        default_chapter_model = default_model_row.scalar_one_or_none()
        if replace:
            await s.execute(
                sa.text("DELETE FROM chapters WHERE run_id=:r"),
                {"r": run_id},
            )
        for i, c in enumerate(chapters):
            stmt = (
                pg_insert(Chapter)
                .values(
                    run_id=run_id,
                    index=i,
                    title=c["title"],
                    summary=c.get("summary"),
                    key_points=c.get("key_points", []),
                    target_pages=c.get("target_pages", 3),
                    model_snapshot=c.get("chapter_model") or default_chapter_model,
                )
                .on_conflict_do_update(
                    index_elements=["run_id", "index"],
                    set_={
                        "title": sa.text("EXCLUDED.title"),
                        "summary": sa.text("EXCLUDED.summary"),
                        "key_points": sa.text("EXCLUDED.key_points"),
                        "target_pages": sa.text("EXCLUDED.target_pages"),
                        "model_snapshot": sa.text("EXCLUDED.model_snapshot"),
                    },
                )
            )
            await s.execute(stmt)


async def flush_chapter_partial(
    run_id: int,
    index: int,
    version_id: int | None,
    partial_text: str,
) -> None:
    """⭐ R-14:章节流式生成中 periodic flush 累积 token 到 DB。

    单事务同步两张表(chapter ↔ chapter_version 不能漂移):
      · ``chapters.final_text = partial_text``(让前端 GET chapter 拿到快照)
      · ``chapter_versions.body_markdown = partial_text``(给 P5 历史版本看)

    ⚠️ ``Chapter.final_text`` 字段 R-14 之前语义是"章节最终通过的正文",
    现在扩展成"generating 期间也存 partial 快照";``status='generating'``
    + final_text != NULL 时表示"流式中,允许读"。``status='approved'`` /
    ``'skipped'`` 时是终态完整正文。

    ``version_id is None``(理论不会发生 — write_chapter 在流之前 already
    调 save_chapter_version 拿到 id)→ 仅 UPDATE chapters,跳 chapter_versions。
    """
    from sqlalchemy import update

    from ..models import Chapter, ChapterVersion

    async with session_factory() as s, s.begin():
        await s.execute(
            update(Chapter)
            .where(Chapter.run_id == run_id, Chapter.index == index)
            .values(final_text=partial_text)
        )
        if version_id is not None:
            await s.execute(
                update(ChapterVersion)
                .where(ChapterVersion.id == version_id)
                .values(body_markdown=partial_text)
            )


async def get_latest_chapter_version_text(
    run_id: int,
    index: int,
) -> str | None:
    """⭐ R-18:取该章节当前最新一条 ChapterVersion 的 body_markdown。

    用法(write_chapter 在 retry / revise 时):**在 ``save_chapter_version``
    pre-create 新占位行之前**调本函数,拿到的就是"上一轮正文" — 因为
    新行尚未插入,MAX(version) 还指向上轮。

    返 None 时:章节不存在 / 没有任何 ChapterVersion(原稿首次生成 retry_count=0
    本就不该走 revise 路径)。

    注:不过滤 abandoned —— retry_failed 路径下旧版本都被标 abandoned=true,
    revise 路径下不标。两种情况"最新版本"都是用户上一轮看到的内容,正是
    LLM 修订需要的输入。
    """
    from sqlalchemy import select

    from ..models import Chapter, ChapterVersion

    async with session_factory() as s:
        chapter_id_row = await s.execute(
            select(Chapter.id).where(
                Chapter.run_id == run_id, Chapter.index == index
            )
        )
        chapter_id = chapter_id_row.scalar_one_or_none()
        if chapter_id is None:
            return None

        row = await s.execute(
            select(ChapterVersion.body_markdown)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()


async def save_chapter_version(
    run_id: int,
    index: int,
    body_markdown: str,
    *,
    feedback_in: str | None = None,
    decision: str | None = None,
) -> int | None:
    """append ChapterVersion(自动取下一个 version 号)。

    返回新 version 行的 id;chapter 找不到返回 None。
    """
    from ..models import Chapter, ChapterVersion

    async with session_factory() as s, s.begin():
        chapter_id_row = await s.execute(
            sa.select(Chapter.id).where(
                Chapter.run_id == run_id, Chapter.index == index
            )
        )
        chapter_id = chapter_id_row.scalar_one_or_none()
        if chapter_id is None:
            log.warning(
                "save_chapter_version_chapter_missing", run_id=run_id, index=index
            )
            return None

        next_version_row = await s.execute(
            sa.text(
                "SELECT COALESCE(MAX(version), 0) + 1 "
                "FROM chapter_versions WHERE chapter_id=:c"
            ),
            {"c": chapter_id},
        )
        next_version = int(next_version_row.scalar_one())
        cv = ChapterVersion(
            chapter_id=chapter_id,
            version=next_version,
            body_markdown=body_markdown,
            feedback_in=feedback_in or None,
            decision=decision,
        )
        s.add(cv)
        await s.flush()
        return cv.id


# ⭐ FR-4.7:retry 等于整章重写,旧版本(不论是否已审)都不再代表最新可用产物。
# 单一信源:本 SQL 同时被 ``mark_chapter_versions_abandoned``(独立 session)和
# ``worker/tasks.py:retry_failed_chapter_task``(嵌入 ReviewEvent + chapter status
# 同事务)使用,WHERE 子句保持一致以避免语义漂移。
_MARK_VERSIONS_ABANDONED_SQL = (
    "UPDATE chapter_versions SET abandoned=true "
    "WHERE chapter_id=:c AND abandoned=false"
)


async def _mark_chapter_versions_abandoned_in_session(
    session: AsyncSession, chapter_id: int
) -> int:
    """在调用方提供的 session 内执行 abandon SQL(不 commit,不开 transaction)。"""
    result = await session.execute(
        sa.text(_MARK_VERSIONS_ABANDONED_SQL), {"c": chapter_id}
    )
    # AsyncSession.execute 在 stub 里返 ``Result[Any]``;DML 实际是 CursorResult,
    # 有 rowcount。cast 一下让 mypy 收敛。
    return cast(CursorResult[Any], result).rowcount or 0


async def mark_chapter_versions_abandoned(chapter_id: int) -> int:
    """FR-4.7:把该章节**所有非 abandoned 版本**标 ``abandoned=true``。
    返回受影响行数。

    本入口走独立 session + commit;worker retry 路径直接走
    ``_mark_chapter_versions_abandoned_in_session`` 复用单事务。
    """
    async with session_factory() as s:
        n = await _mark_chapter_versions_abandoned_in_session(s, chapter_id)
        await s.commit()
        return n


async def record_review_event(
    chapter_id: int,
    reviewer_id: int,
    decision: str,
    *,
    feedback_text: str | None = None,
) -> int:
    """写 ReviewEvent(P5 三按钮 / API /review 调)。返回新行 id。"""
    from ..models import ReviewEvent

    async with session_factory() as s, s.begin():
        ev = ReviewEvent(
            chapter_id=chapter_id,
            reviewer_id=reviewer_id,
            decision=decision,
            feedback_text=feedback_text,
        )
        s.add(ev)
        await s.flush()
        return ev.id


async def publish_event(project_id: int, type_: str, **payload: Any) -> None:
    try:
        await event_bus.publish(project_id, {"type": type_, **payload})
    except Exception:
        log.exception("event_publish_failed", project_id=project_id, type=type_)
