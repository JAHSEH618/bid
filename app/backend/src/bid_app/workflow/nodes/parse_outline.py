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

D-EF (2026-05-18):若 ``state.template_pack`` 非空,加载骨架并反查 leaf
节点的 ``chapter_type / template_slot / required_anchors`` 兜底填充
(LLM-1 漏写字段时也能恢复)。展平后比对骨架 ``fixed`` 叶子是否齐全,
缺失时记录 warning(不抛异常,保留给 outline_review 让用户决定)。
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ..state import WorkflowState
from ..templates import (
    build_title_path_index,
    fixed_leaves_with_nodes,
    load_pack,
)

log = structlog.get_logger()


def _normalize_chapter(
    ch: dict[str, Any],
    section: str,
    idx: int,
    parent_titles: list[str],
) -> dict[str, Any]:
    """把单个叶子节点 normalize 成 chapters[] 一条记录。

    ``section`` 是层级编号 "1.1" / "2.3.1";``idx`` 是展平后的 0-based 位置,
    用作 fallback id;``parent_titles`` 是从根到父节点的祖先标题列表
    (chaptersToTocText 重建分组行时用)。

    D-EF:``chapter_type`` / ``template_slot`` / ``required_anchors`` 三字段
    默认值为 ``normal`` / ``""`` / ``[]``,由骨架 overlay 阶段(``_apply_skeleton_overlay``)
    覆盖。
    """
    ch.setdefault("id", f"ch_{idx + 1:02d}")
    ch["section"] = section
    ch["parent_titles"] = list(parent_titles)
    ch.setdefault("title", f"第 {section} 节")
    ch.setdefault("summary", "")
    ch.setdefault("key_points", [])
    ch.setdefault("target_pages", 3)
    ch.setdefault("matched_scoring_items", [])
    # D-EF:章节类型默认值
    ch.setdefault("chapter_type", "normal")
    ch.setdefault("template_slot", "")
    ch.setdefault("required_anchors", [])

    if not isinstance(ch["target_pages"], (int, float)):
        try:
            ch["target_pages"] = int(ch["target_pages"])
        except (ValueError, TypeError):
            ch["target_pages"] = 3
    if not isinstance(ch["key_points"], list):
        ch["key_points"] = [str(ch["key_points"])]
    if not isinstance(ch["matched_scoring_items"], list):
        ch["matched_scoring_items"] = []
    if not isinstance(ch["required_anchors"], list):
        ch["required_anchors"] = []
    if not isinstance(ch["chapter_type"], str) or not ch["chapter_type"]:
        ch["chapter_type"] = "normal"
    if not isinstance(ch["template_slot"], str):
        ch["template_slot"] = ""
    return ch


_ALLOWED_CHAPTER_TYPES = {
    "normal", "module", "principle", "architecture",
    "meeting", "image_only", "table_only",
}


def _apply_skeleton_overlay(
    chapters: list[dict[str, Any]],
    template_pack: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """根据骨架反查每个叶子的 chapter_type / template_slot / required_anchors。

    匹配规则:按 ``(parent_titles..., title)`` 完整路径在骨架索引里查;
    精确命中 → 用骨架值覆盖 LLM-1 输出(LLM 偶尔会漏写或写错);未命中
    → 保留 LLM-1 已有字段或 _normalize_chapter 的默认值。

    校验:遍历骨架里所有 ``fixed`` 叶子,若任何一条没在 chapters 里找到
    匹配(按标题路径),记入 ``warnings`` 返回。
    """
    warnings: list[str] = []
    if not template_pack:
        return chapters, warnings

    try:
        pack = load_pack(template_pack)
    except FileNotFoundError:
        log.warning("parse_outline_skeleton_missing", pack=template_pack)
        return chapters, [f"骨架包不存在: {template_pack}"]

    skeleton = pack.get("skeleton") or []
    if not isinstance(skeleton, list):
        return chapters, []

    path_index = build_title_path_index(skeleton)
    # 同时建一份「叶子标题 → leaf」的兜底索引(对 LLM 偶尔挪动祖先标题的容错)
    by_title: dict[str, dict[str, Any]] = {}
    for full_path, leaf in path_index.items():
        title = full_path[-1] if full_path else ""
        if title:
            by_title.setdefault(title, leaf)

    for ch in chapters:
        title = str(ch.get("title") or "")
        parents = tuple(ch.get("parent_titles") or [])
        key = (*parents, title)
        leaf_match: dict[str, Any] | None = path_index.get(key) or by_title.get(title)
        if not leaf_match:
            continue
        # 骨架值覆盖 LLM(LLM 偶尔写错 chapter_type 名,不能信任)
        ct = leaf_match.get("chapter_type")
        if isinstance(ct, str) and ct in _ALLOWED_CHAPTER_TYPES:
            ch["chapter_type"] = ct
        slot = leaf_match.get("template_slot")
        if isinstance(slot, str) and slot:
            ch["template_slot"] = slot
        req = leaf_match.get("required_anchors")
        if isinstance(req, list):
            ch["required_anchors"] = [str(x) for x in req]
        # target_pages 骨架有就用骨架的(LLM 的 target_pages 容易飘)
        tp = leaf_match.get("target_pages")
        if isinstance(tp, (int, float)) and tp > 0:
            ch["target_pages"] = int(tp)

    # 校验 + 自动补齐:骨架里所有 fixed 叶子是否齐全
    # - expandable 叶子(``类似业绩`` 等):LLM 把它展开成多个子叶,
    #   chapters[i].parent_titles 里会出现这个标题,视为已展开
    # - 非 expandable 叶子:LLM 必须照抄,否则自动补一条到末尾
    have_paths: set[tuple[str, ...]] = {
        (*tuple(ch.get("parent_titles") or []), str(ch.get("title") or ""))
        for ch in chapters
    }
    have_titles = {str(ch.get("title") or "") for ch in chapters}
    have_parent_titles: set[str] = set()
    for ch in chapters:
        for p in ch.get("parent_titles") or []:
            if isinstance(p, str):
                have_parent_titles.add(p)

    injected = 0
    for path, leaf_node in fixed_leaves_with_nodes(skeleton):
        leaf_title = path[-1] if path else ""
        # 已存在(精确路径 / 同标题兜底)→ 跳过
        if path in have_paths or leaf_title in have_titles:
            continue
        # expandable 叶子:LLM 展开后,此 title 应出现在某些 chapter 的
        # parent_titles 里(作为它们的祖先),也视为已处理
        if leaf_node.get("expandable") and leaf_title in have_parent_titles:
            continue

        # 真缺失:把骨架定义补一条到末尾,parent_titles 复用骨架的祖先链
        parent_titles_list = list(path[:-1])
        target_pages = leaf_node.get("target_pages", 1)
        if not isinstance(target_pages, (int, float)) or target_pages <= 0:
            target_pages = 1
        required = leaf_node.get("required_anchors") or []
        if not isinstance(required, list):
            required = []

        new_chapter: dict[str, Any] = {
            "title": leaf_title,
            "summary": "",
            "key_points": [],
            "matched_scoring_items": [],
            "target_pages": int(target_pages),
            "chapter_type": leaf_node.get("chapter_type") or "normal",
            "template_slot": leaf_node.get("template_slot") or "",
            "required_anchors": [str(x) for x in required],
        }
        next_idx = len(chapters)
        # section 号给个不太冲突的尾标 ``99.{n}``,避免与 LLM 已用的 1.x/2.x
        # 冲突;outline_review 用户可以在 textarea 里改成想要的层级
        new_chapter = _normalize_chapter(
            new_chapter,
            section=f"99.{injected + 1}",
            idx=next_idx,
            parent_titles=parent_titles_list,
        )
        chapters.append(new_chapter)
        warnings.append(f"自动补齐 fixed 叶子: {' / '.join(path)}")
        injected += 1

    return chapters, warnings


def _flatten_toc(toc: list[Any]) -> list[dict[str, Any]]:
    """展平层级目录:有 ``children`` 的节点是分组(只用来给后代编号),
    没有 ``children`` 或 children 为空的节点是叶子,加进结果列表。

    section 编号按深度优先生成:一级 "1" / "2",二级 "1.1" / "1.2",
    三级 "1.1.1" 以此类推。**只有叶子进入结果**(下游 write_chapter 只
    生成叶子);分组的 title 会被记录到每个后代叶子的 ``parent_titles``
    数组里,让前端 textarea 重建分组行。
    """
    leaves: list[dict[str, Any]] = []
    idx_counter = [0]

    def walk(nodes: list[Any], prefix: list[int], titles: list[str]) -> None:
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            path = [*prefix, i + 1]
            section = ".".join(str(n) for n in path)
            children = node.get("children")
            if isinstance(children, list) and len(children) > 0:
                # 分组:把自己的 title 加到 titles 栈,继续往下走
                walk(children, path, [*titles, str(node.get("title") or "")])
            else:
                # 叶子:加入结果
                leaf = dict(node)
                leaf.pop("children", None)
                leaves.append(_normalize_chapter(leaf, section, idx_counter[0], titles))
                idx_counter[0] += 1

    walk(toc, [], [])
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
        normalized.append(_normalize_chapter(dict(ch), str(i + 1), i, []))
    return normalized


async def run(state: WorkflowState) -> dict[str, Any]:
    outline_json = state.get("_outline_json", "")
    chapters = _normalize(outline_json)

    # D-EF:骨架 overlay — 反查模版骨架填充 chapter_type 等;校验 fixed 叶子完整性
    template_pack = state.get("template_pack")
    skeleton_warnings: list[str] = []
    if chapters and template_pack:
        chapters, skeleton_warnings = _apply_skeleton_overlay(chapters, template_pack)

    if not chapters:
        log.warning(
            "parse_outline_empty",
            project_id=state.get("project_id"),
            run_id=state.get("run_id"),
        )

    if skeleton_warnings:
        log.warning(
            "parse_outline_skeleton_violations",
            project_id=state.get("project_id"),
            run_id=state.get("run_id"),
            pack=template_pack,
            missing=skeleton_warnings,
        )

    return {
        "chapters": chapters,
        "current_index": 0,
        "retry_count": 0,
        "finalized_chapters": [],
        "revision_feedback": "",
    }
