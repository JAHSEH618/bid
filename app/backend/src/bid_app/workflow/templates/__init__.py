"""模版骨架包(D-EF)。

每个 ``*.json`` 文件是一份"模版骨架":固定 H1 顺序 + 各章节的
``chapter_type`` / ``template_slot`` / ``required_anchors``,LLM-1 在该骨架
基础上做裁剪与展开,而不是从零设计目录。

骨架顶层 schema(.json):
    {
      "id": "<pack-id>",
      "title": "<pack-中文标题>",
      "description": "<适用范围>",
      "principles": [...],            # design principle 章节用到的固定 5 条
      "architecture_layers": [...],   # architecture 章节用到的固定层名
      "performance_case_h3_slots": [...],
      "qualification_certs": [...],
      "skeleton": [
        {
          "title": "<标题>",
          "fixed": true | false,
          "chapter_type": "module|principle|architecture|meeting|image_only|table_only|normal",
          "template_slot": "<稳定 slot id>",
          "required_anchors": [...],
          "target_pages": int,
          "children": [...],
          "expandable": bool,
          "child_chapter_type": "<推荐子 chapter_type>",
          "expand_min": int,
          "expand_max": int
        }
      ]
    }

加载与查询:
    >>> from bid_app.workflow.templates import load_pack, pick_pack
    >>> pack = pick_pack("gov_consumer_platform")
    >>> pack["skeleton"][0]["title"]
    '评审索引表'
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_TEMPLATES_DIR = Path(__file__).parent

DEFAULT_PACK_ID = "gov_consumer_platform_v1"

# project_category(LLM-0 在 material_understanding 输出) → pack id 的兜底映射。
# pack id 不存在时全部回落到 DEFAULT_PACK_ID。
_CATEGORY_TO_PACK: dict[str, str] = {
    "gov_consumer_platform": "gov_consumer_platform_v1",
    "smart_city": "gov_consumer_platform_v1",  # 暂复用,后续可分包
    "ticketing": "gov_consumer_platform_v1",
    "financial_system": "gov_consumer_platform_v1",
}


@lru_cache(maxsize=8)
def load_pack(pack_id: str) -> dict[str, Any]:
    """读取骨架 JSON。pack_id 不存在时抛 FileNotFoundError。"""
    path = _TEMPLATES_DIR / f"{pack_id}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def pick_pack(project_category: str | None) -> dict[str, Any]:
    """按 material_understanding 输出的 project_category 选骨架。

    未知类别 / None → 回落到 DEFAULT_PACK_ID。Pack id 文件缺失也回落。
    """
    candidate = _CATEGORY_TO_PACK.get(project_category or "", DEFAULT_PACK_ID)
    try:
        return load_pack(candidate)
    except FileNotFoundError:
        return load_pack(DEFAULT_PACK_ID)


def iter_skeleton_leaves(skeleton: list[dict[str, Any]]) -> list[tuple[list[str], dict[str, Any]]]:
    """深度优先遍历骨架,返回 ``[(parent_titles, leaf_node), ...]``。

    ``leaf_node`` 是没有 ``children`` 或 children 为空的节点(可能是
    ``expandable`` 的占位节点,也可能是真叶子)。``parent_titles`` 是从根
    到该叶子的祖先标题列表(不含叶子自身)。
    """
    out: list[tuple[list[str], dict[str, Any]]] = []

    def walk(nodes: list[dict[str, Any]], titles: list[str]) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            children = node.get("children")
            if isinstance(children, list) and children:
                walk(children, [*titles, str(node.get("title") or "")])
            else:
                out.append((list(titles), node))

    walk(skeleton, [])
    return out


def build_title_path_index(
    skeleton: list[dict[str, Any]],
) -> dict[tuple[str, ...], dict[str, Any]]:
    """构建 ``(祖先标题..., 叶子标题) → leaf_node`` 索引,供 parse_outline 反查。"""
    return {
        (*parent_titles, str(leaf.get("title") or "")): leaf
        for parent_titles, leaf in iter_skeleton_leaves(skeleton)
    }


def fixed_leaf_paths(skeleton: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """返回所有 ``fixed: true`` 叶子的完整标题路径,parse_outline 据此校验。"""
    return [
        (*parent_titles, str(leaf.get("title") or ""))
        for parent_titles, leaf in iter_skeleton_leaves(skeleton)
        if leaf.get("fixed") is True
    ]


def fixed_leaves_with_nodes(
    skeleton: list[dict[str, Any]],
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    """同 ``fixed_leaf_paths``,但返回完整 ``(path, leaf_node)`` 对,供
    parse_outline 区分 expandable / 非 expandable 叶子做不同处理。
    """
    return [
        ((*parent_titles, str(leaf.get("title") or "")), leaf)
        for parent_titles, leaf in iter_skeleton_leaves(skeleton)
        if leaf.get("fixed") is True
    ]


__all__ = [
    "DEFAULT_PACK_ID",
    "build_title_path_index",
    "fixed_leaf_paths",
    "fixed_leaves_with_nodes",
    "iter_skeleton_leaves",
    "load_pack",
    "pick_pack",
]
