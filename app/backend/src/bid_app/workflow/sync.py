"""state ↔ DB 同步钩子(§10.6 + §10.6d)。

把 LangGraph WorkflowState 的变化同步到 DB Chapter / Project 表 + 发 SSE 事件。
LangGraph state 是 in-memory + checkpoint 持久化,但前端展示用的是 DB,
两层必须保持一致。

⚠️ M0 时 chapters / projects / runs 表还不存在(M1 落库),节点调用这些
helper 在 sqlite/无 DB 跑会报错;M0 CLI ``run_local`` 用本地状态而不入 DB,
跳过这些 helper(直接构造完整 state)。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
    from ..models import Chapter  # type: ignore[attr-defined]

    async with session_factory() as s, s.begin():
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
                )
                .on_conflict_do_update(
                    index_elements=["run_id", "index"],
                    set_={
                        "title": sa.text("EXCLUDED.title"),
                        "summary": sa.text("EXCLUDED.summary"),
                        "key_points": sa.text("EXCLUDED.key_points"),
                        "target_pages": sa.text("EXCLUDED.target_pages"),
                    },
                )
            )
            await s.execute(stmt)


async def publish_event(project_id: int, type_: str, **payload: Any) -> None:
    try:
        await event_bus.publish(project_id, {"type": type_, **payload})
    except Exception:
        log.exception("event_publish_failed", project_id=project_id, type=type_)
