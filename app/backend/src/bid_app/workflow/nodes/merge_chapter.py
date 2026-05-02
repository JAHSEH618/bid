"""章节合并节点(v10 §4.5.4 / Spec §10.2 ``merge_chapter``)。

把 LLM-2 正文 + LLM-3 可视化建议 JSON 拼成 ``full_chapter`` markdown,塞回
``state._pending_chapter_text``,供下游 ``human_review`` interrupt 把完整
markdown 推给前端预览。

⚠️ 本节点**仅**做模板转换,**不**调 interrupt;P5 人工审核走单独的
``human_review`` 节点(D-EE 拆分,§10.2 / §10.6b)。
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from ..state import WorkflowState

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
    idx = state["current_index"]
    chapter = state["chapters"][idx]

    full_chapter = _render_full_chapter(
        chapter_index=idx,
        chapter_title=chapter.get("title", ""),
        chapter_text=state.get("_pending_chapter_text", ""),
        visuals_json_str=state.get("_pending_visuals_json", '{"items": []}'),
    )
    return {"_pending_chapter_text": full_chapter}
