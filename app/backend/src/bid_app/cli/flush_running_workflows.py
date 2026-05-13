"""清退 v1 残留项目的应急 CLI (PR-M7-1 / D1)。

v2 上线时,所有 ``status IN ('running', 'awaiting_review', 'queued',
'extracting', 'outlining', 'outline_ready')`` 的项目都需要标 ``aborted_v1``,
因为其 LangGraph checkpoint 是 v1 schema,在 v2 graph 上无法 resume。

用法::

    docker compose exec app python -m bid_app.cli.flush_running_workflows --confirm

被标记的项目:
- 用户在 UI 上看到「v1 → v2 已升级,该项目需重建」提示。
- 不删除原始数据(documents / chapters / runs 保留),仅切 status。
- 历史 ``done`` / ``failed`` / ``aborted`` 项目不受影响。
"""

from __future__ import annotations

import asyncio

import click
import sqlalchemy as sa

from ..db import session_factory

# 仍可能驱动 workflow 的 status 集合(对齐 models/project.py 注释)
_INFLIGHT_STATUSES: tuple[str, ...] = (
    "init",
    "extracting",
    "outlining",
    "outline_ready",
    "queued",
    "running",
    "awaiting_review",
)


async def _count_inflight() -> int:
    async with session_factory() as s:
        row = await s.execute(
            sa.text(
                "SELECT COUNT(*) FROM projects WHERE status = ANY(:s)"
            ),
            {"s": list(_INFLIGHT_STATUSES)},
        )
        return int(row.scalar_one())


async def _flush() -> int:
    async with session_factory() as s:
        result = await s.execute(
            sa.text(
                "UPDATE projects SET status='aborted_v1' "
                "WHERE status = ANY(:s) "
                "RETURNING id"
            ),
            {"s": list(_INFLIGHT_STATUSES)},
        )
        ids = [row[0] for row in result.fetchall()]
        # 同步把这些项目的 active run 标 aborted,清理 worker 视角
        if ids:
            await s.execute(
                sa.text(
                    "UPDATE runs SET status='aborted', "
                    "finished_at=NOW(), error='v1 → v2 upgrade flush' "
                    "WHERE project_id = ANY(:ids) AND status='running'"
                ),
                {"ids": ids},
            )
        await s.commit()
        return len(ids)


@click.command()
@click.option(
    "--confirm",
    is_flag=True,
    help="不加这个 flag 只会 dry-run 报数,不写库。",
)
def main(confirm: bool) -> None:
    inflight = asyncio.run(_count_inflight())
    if inflight == 0:
        click.echo("No in-flight v1 projects to flush.")
        return

    click.echo(f"Found {inflight} in-flight project(s) to flush.")
    if not confirm:
        click.echo(
            "Dry run only. Re-run with --confirm to actually mark them aborted_v1."
        )
        return

    flushed = asyncio.run(_flush())
    click.echo(
        f"Marked {flushed} project(s) as 'aborted_v1'. "
        "Users will be prompted to recreate them."
    )


if __name__ == "__main__":
    main()
