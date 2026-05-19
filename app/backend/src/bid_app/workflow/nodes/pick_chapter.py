"""选取当前章节(对应 v10 §4.5.1 + PR-M9-1 选择性生成)。

PR-M9-1 / D4:
- 若 ``state.selected_chapter_ids`` 非空,只处理 id 命中的章节;
  未命中的章节直接 mark status='skipped',同步 advance current_index,
  不进入下游 write_chapter / gen_visuals / human_review。
- selected 为 None / 空列表 → 全选(向后兼容旧 checkpoint)。
- 章节编号保留原序号 (D4):跳过的 ch_02 不会被 ch_03 抢占,
  assemble 输出还是 "ch_01 / ch_03 / ch_05"。

实现:单次调用内连续跳过所有 unselected 直到落到一个 selected 章节
(或越界)。下游 chapter_generate_gate / update_state 不需要再判断
selection,保持节点解耦。
"""

from __future__ import annotations

from typing import Any

import structlog

from ..state import WorkflowState
from ..sync import publish_event, sync_chapter_to_db

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = state["project_id"]
    run_id = state.get("run_id")
    idx = state["current_index"]
    chapters = state.get("chapters") or []
    selected_ids = state.get("selected_chapter_ids") or None
    use_selection = bool(selected_ids)
    finalize_early = bool(state.get("_finalize_early"))

    # D-EM:用户点了「完成评审,提前合并」→ 把剩余未生成章节全部标 not_generated,
    # current_index 一次推到末尾,assemble 节点会插入「（本章未生成）」占位
    if finalize_early and idx < len(chapters):
        marked = 0
        # 在 finalized_chapters 尾部追加占位文字,保留章节标题与编号
        finalized = list(state.get("finalized_chapters") or [])
        for i in range(idx, len(chapters)):
            ch = chapters[i]
            section = ch.get("section") or str(i + 1)
            title = ch.get("title", "(未命名章节)")
            placeholder = (
                f"## {section} {title}\n\n"
                f"> **（本章未生成）** 该章节在用户提前合并时尚未生成正文。\n"
            )
            finalized.append(placeholder)
            if isinstance(run_id, int) and run_id > 0:
                try:
                    await sync_chapter_to_db(
                        run_id,
                        i,
                        status="not_generated",
                        processing_started_at=None,
                        final_text=placeholder,
                    )
                except Exception:
                    log.exception(
                        "pick_chapter_finalize_early_sync_failed",
                        run_id=run_id,
                        idx=i,
                    )
            await publish_event(
                project_id,
                "chapter_not_generated",
                chapter_index=i,
                chapter_title=title,
            )
            marked += 1
        log.info(
            "pick_chapter_finalize_early",
            project_id=project_id,
            marked=marked,
            new_index=len(chapters),
        )
        return {
            "current_index": len(chapters),
            "retry_count": 0,
            "finalized_chapters": finalized,
            # 清掉 flag,避免下次 resume 误触发
            "_finalize_early": False,
        }

    advanced = 0

    while idx < len(chapters):
        current = chapters[idx]
        chapter_id = current.get("id") or current.get("chapter_id")

        if not use_selection or (
            chapter_id and selected_ids and chapter_id in selected_ids
        ):
            # 命中或未启用选择 → 正常推下游
            break

        # ⭐ PR-M9-1 / D4:未命中 → 标 skipped + 推下一章
        await publish_event(
            project_id,
            "chapter_skipped_unselected",
            chapter_index=idx,
            chapter_id=chapter_id,
            chapter_title=current.get("title"),
        )
        if isinstance(run_id, int) and run_id > 0:
            try:
                await sync_chapter_to_db(
                    run_id,
                    idx,
                    status="skipped",
                    processing_started_at=None,
                )
            except Exception:
                log.exception(
                    "pick_chapter_skip_sync_failed",
                    run_id=run_id,
                    idx=idx,
                )
        idx += 1
        advanced += 1

    if advanced > 0:
        log.info(
            "pick_chapter_skipped_unselected_batch",
            project_id=project_id,
            advanced=advanced,
            new_index=idx,
        )

    if idx >= len(chapters):
        # 全部走完;route_after_update 已在 update_state 后处理,但 pick
        # 阶段直接到达 assemble 边界时也要保证 current_index 写回 state。
        log.info(
            "pick_chapter_exhausted",
            project_id=project_id,
            idx=idx,
            total=len(chapters),
        )
        return {"current_index": idx, "retry_count": 0}

    current = chapters[idx]
    await publish_event(
        project_id,
        "chapter_picked",
        chapter_index=idx,
        chapter_title=current.get("title"),
        retry_count=state.get("retry_count", 0),
    )

    if advanced > 0:
        return {"current_index": idx, "retry_count": 0}
    return {}
