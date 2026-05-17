"""单章 DOCX 缓存失效辅助(2026-05 review #37)。

单章导出走 ``chapter_{id}.docx`` 缓存 + ``DocxJob(scope='chapter')`` 状态机:
只要 latest job done + 文件存在,API 端就返 ``cached=True``。但章节正文
``Chapter.final_text`` 会在多处被覆盖 / 清空:

- ``_prepare_chapter_body_generation``(主流程 + 预生成)
- ``retry_failed_chapter_task``
- ``api/chapters.py`` 切模型时 ``clear_prefetch=True``
- 流式 ``flush_chapter_partial`` 中途也会写

旧 chapter_{id}.docx 与新 final_text 内容不一致,但缓存命中仍返回旧 DOCX。
本 helper 在 final_text 即将改变 / 已改变的写点调一次,把:
1. ``docx_jobs WHERE chapter_id=? AND scope='chapter' AND status IN
   ('done','pending','rendering_mermaid','pandoc','finalizing')`` 全部
   标 ``invalidated``(对齐 D-CG 整本失效语义);
2. unlink ``{project_dir}/chapter_{chapter_id}.docx`` 文件(best-effort,
   失败仅 log)。

下次用户点单章导出时,latest job 不是 done → trigger 路径重建,缓存自动
跟上最新 final_text。
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
import structlog

from ..db import session_factory

log = structlog.get_logger()


async def invalidate_chapter_docx(chapter_id: int) -> None:
    """标 chapter 范围所有未终结 docx_jobs 为 invalidated,并删除产物文件。

    幂等;失败仅 log,不抛(调用方都是 best-effort 失效场景:章节正在被
    重新生成 / 重试 / 切模型,DOCX 缓存对错都不影响主流程,但留着会让用户
    下载到旧版本)。
    """
    try:
        async with session_factory() as s:
            rows = (
                (
                    await s.execute(
                        sa.text(
                            """
                            UPDATE docx_jobs SET status='invalidated',
                                finished_at=COALESCE(finished_at, NOW()),
                                updated_at=NOW()
                            WHERE chapter_id=:c AND scope='chapter'
                              AND status IN ('done','pending','rendering_mermaid',
                                             'pandoc','finalizing')
                            RETURNING id, output_path
                            """
                        ),
                        {"c": chapter_id},
                    )
                )
                .mappings()
                .all()
            )

            project_dir_str = (
                await s.execute(
                    sa.text(
                        "SELECT p.dir_path FROM chapters c "
                        "JOIN runs r ON r.id = c.run_id "
                        "JOIN projects p ON p.id = r.project_id "
                        "WHERE c.id=:c"
                    ),
                    {"c": chapter_id},
                )
            ).scalar_one_or_none()

            await s.commit()
    except Exception:
        log.exception("invalidate_chapter_docx_db_failed", chapter_id=chapter_id)
        return

    if not rows and not project_dir_str:
        return

    # unlink cached file;output_path 可能是 invalidated 之前写的真实路径,
    # 也可能是 NULL(还没跑到 done)。优先按 dir_path/chapter_{id}.docx 拼
    # 路径(单章稳定命名),再兜底每条 row 的 output_path。
    candidates: set[Path] = set()
    if project_dir_str:
        candidates.add(Path(project_dir_str) / f"chapter_{chapter_id}.docx")
    for row in rows:
        op = row.get("output_path")
        if op:
            candidates.add(Path(op))

    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            log.exception(
                "invalidate_chapter_docx_unlink_failed",
                chapter_id=chapter_id,
                path=str(path),
            )

    if rows:
        log.info(
            "chapter_docx_invalidated",
            chapter_id=chapter_id,
            job_ids=[r["id"] for r in rows],
        )
