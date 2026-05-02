"""章节合并 + P5 人工审核 interrupt 节点(对应 v10 §4.5.4 + §4.5.5
+ Spec §10.6b ``human_review``)。

任务清单合并了 v10 的两个职责:
  ① 模板转换合并:把 LLM-2 正文 + LLM-3 可视化建议拼成 ``full_chapter`` markdown
  ② Human Input 三按钮(approve / revise / skip):interrupt 暂停等用户审核

resume payload 形状::

    {"decision": "approve" | "revise" | "skip",
     "feedback": "..."}    # 选 revise 时必填,审核者写的修改建议

下游 ``update_state`` 节点接 ``_review_decision`` / ``_review_feedback``。
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from langgraph.types import interrupt

from ..state import WorkflowState
from ..sync import publish_event, sync_project_status

log = structlog.get_logger()


def _render_full_chapter(
    *,
    chapter_index: int,
    chapter_title: str,
    chapter_text: str,
    visuals_json_str: str,
) -> str:
    """v10 §4.5.4 模板转换的 Python 等价实现。"""
    parts: list[str] = [
        f"## 第 {chapter_index + 1} 章 · {chapter_title}",
        "",
        chapter_text.strip(),
        "",
    ]

    items: list[dict[str, Any]] = []
    try:
        loaded = json.loads(visuals_json_str or "{}")
        if isinstance(loaded, dict):
            items = loaded.get("items") or []
    except json.JSONDecodeError:
        log.warning(
            "merge_chapter_visuals_json_invalid",
            head=(visuals_json_str or "")[:120],
        )

    if items:
        parts.extend(["---", "", "### 📊 本章可视化元素", ""])
        for i, v in enumerate(items, start=1):
            v_title = v.get("title") or f"可视化 {i}"
            v_anchor = v.get("anchor", "")
            v_position = v.get("position", "")
            v_type = v.get("type", "ascii")
            v_content = v.get("content", "")

            parts.append(f"#### {i}. {v_title}")
            parts.append("")
            parts.append(f"**插入位置**:`{v_anchor}` ({v_position})")
            parts.append("")
            if v_type == "mermaid":
                parts.append("```mermaid")
                parts.append(v_content)
                parts.append("```")
            elif v_type == "table":
                parts.append(v_content)
            else:  # ascii / 其他
                parts.append("```")
                parts.append(v_content)
                parts.append("```")
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    idx = state["current_index"]
    chapter = state["chapters"][idx]

    # ① 合并正文 + 可视化
    full_chapter = _render_full_chapter(
        chapter_index=idx,
        chapter_title=chapter.get("title", ""),
        chapter_text=state.get("_pending_chapter_text", ""),
        visuals_json_str=state.get("_pending_visuals_json", '{"items": []}'),
    )

    # ② 项目切 awaiting_review,SSE 通知前端拉章节
    await sync_project_status(pid, "awaiting_review")
    await publish_event(
        pid,
        "awaiting_review",
        chapter_index=idx,
        chapter_text=full_chapter,
    )

    # ③ interrupt 等审核(P5 三按钮)
    payload = interrupt(
        {
            "kind": "chapter_review",
            "chapter_index": idx,
            "chapter_text": full_chapter,
        }
    )

    # resume 后:project 回 running;decision/feedback 写进 state 给 update_state
    await sync_project_status(pid, "running")
    return {
        "_pending_chapter_text": full_chapter,  # 替换为合并后的完整 md
        "_review_decision": (payload or {}).get("decision", "approve"),
        "_review_feedback": (payload or {}).get("feedback", ""),
    }
