"""提纲解析节点(移植 v10 §4.4)。

输入:``state._outline_json``(LLM-1 原始 JSON 字符串)。
输出:``state.chapters``(标准化后 list[dict])+ ``current_index=0``。

容错:
- 去除可能的 markdown 代码块包裹
- 兜底:抽取第一个 ``{`` 到最后一个 ``}``
- 字段缺失逐项 setdefault,target_pages 强制 int
- key_points / matched_scoring_items 强制 list
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ..state import WorkflowState

log = structlog.get_logger()


def _normalize(outline_json: str) -> list[dict[str, Any]]:
    text = (outline_json or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    chapters = data.get("chapters", []) if isinstance(data, dict) else []

    normalized: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        ch.setdefault("id", f"ch_{i + 1:02d}")
        ch.setdefault("title", f"第 {i + 1} 章")
        ch.setdefault("summary", "")
        ch.setdefault("key_points", [])
        ch.setdefault("target_pages", 3)
        ch.setdefault("matched_scoring_items", [])

        if not isinstance(ch["target_pages"], (int, float)):
            try:
                ch["target_pages"] = int(ch["target_pages"])
            except (ValueError, TypeError):
                ch["target_pages"] = 3
        if not isinstance(ch["key_points"], list):
            ch["key_points"] = [str(ch["key_points"])]
        if not isinstance(ch["matched_scoring_items"], list):
            ch["matched_scoring_items"] = []

        normalized.append(ch)

    return normalized


async def run(state: WorkflowState) -> dict[str, Any]:
    outline_json = state.get("_outline_json", "")
    chapters = _normalize(outline_json)

    if not chapters:
        log.warning(
            "parse_outline_empty",
            project_id=state.get("project_id"),
            run_id=state.get("run_id"),
        )

    return {
        "chapters": chapters,
        "current_index": 0,
        "retry_count": 0,
        "finalized_chapters": [],
        "revision_feedback": "",
    }
