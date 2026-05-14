"""提纲解析节点(移植 v10 §4.4 + PR-M8-2 follow-up 层级目录展平)。

输入:``state._outline_json``(LLM-1 原始 JSON 字符串)。
输出:``state.chapters``(扁平叶子列表,每条带 ``section`` 编号)+ ``current_index=0``。

LLM-1 输出格式(新):
    {"toc": [
        {"title": "一级章名", "children": [
            {"title": "1.1 小节", "key_points": [...], "target_pages": 2, ...},
            ...
        ]},
        ...
    ]}

向下兼容:若 LLM-1 仍返回 v1 的 ``{"chapters": [...]}`` 扁平结构,按扁平
处理,``section`` 字段按 index+1 兜底("1" / "2" / ...)。

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


def _normalize_chapter(ch: dict[str, Any], section: str, idx: int) -> dict[str, Any]:
    """把单个叶子节点 normalize 成 chapters[] 一条记录。

    ``section`` 是层级编号 "1.1" / "2.3.1";``idx`` 是展平后的 0-based 位置,
    用作 fallback id。
    """
    ch.setdefault("id", f"ch_{idx + 1:02d}")
    ch["section"] = section
    ch.setdefault("title", f"第 {section} 节")
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
    return ch


def _flatten_toc(toc: list[Any]) -> list[dict[str, Any]]:
    """展平层级目录:有 ``children`` 的节点是分组(只用来给后代编号),
    没有 ``children`` 或 children 为空的节点是叶子,加进结果列表。

    section 编号按深度优先生成:一级 "1" / "2",二级 "1.1" / "1.2",
    三级 "1.1.1" 以此类推。**只有叶子进入结果**(下游 write_chapter 只
    生成叶子);分组的 title 在 frontend 重建层级时通过 section 前缀
    匹配显示。
    """
    leaves: list[dict[str, Any]] = []
    idx_counter = [0]

    def walk(nodes: list[Any], prefix: list[int]) -> None:
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            path = [*prefix, i + 1]
            section = ".".join(str(n) for n in path)
            children = node.get("children")
            if isinstance(children, list) and len(children) > 0:
                walk(children, path)
            else:
                # 叶子:加入结果
                leaf = dict(node)
                leaf.pop("children", None)
                leaves.append(_normalize_chapter(leaf, section, idx_counter[0]))
                idx_counter[0] += 1

    walk(toc, [])
    return leaves


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

    if not isinstance(data, dict):
        return []

    # 优先走新 schema:{"toc": [...]} 层级结构
    toc = data.get("toc")
    if isinstance(toc, list) and toc:
        return _flatten_toc(toc)

    # 向后兼容:旧 schema {"chapters": [...]} 扁平结构,每条按 idx+1 兜底 section
    legacy = data.get("chapters", [])
    if not isinstance(legacy, list):
        return []
    normalized: list[dict[str, Any]] = []
    for i, ch in enumerate(legacy):
        if not isinstance(ch, dict):
            continue
        normalized.append(_normalize_chapter(dict(ch), str(i + 1), i))
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
