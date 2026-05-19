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
    # D-EP (2026-05-19):缺失的 fixed 叶子按骨架定义位置插入,而不是统统塞末尾用 99.{n}。
    # 旧行为(章节 6 之后突然冒出 99.1/99.2)目录断号严重,影响用户体验。
    # 现在:H1 级叶子在 chapters 列表里找最后一个"骨架顺序在它之前"的章节插在其后;
    # 二级叶子(parent_titles 非空)在 chapters 里找父 H1 的最后一个子节插在其后。
    # 全部插入完毕后调 _renumber_sections 重新按列表顺序派生 section 编号,
    # 保证 1, 1.1, 1.2, 2, 2.1, ... 连续。
    skeleton_h1_titles = [
        str(node.get("title") or "") for node in skeleton if isinstance(node, dict)
    ]
    for path, leaf_node in fixed_leaves_with_nodes(skeleton):
        leaf_title = path[-1] if path else ""
        # 已存在(精确路径 / 同标题兜底)→ 跳过
        if path in have_paths or leaf_title in have_titles:
            continue
        # expandable 叶子:LLM 展开后,此 title 应出现在某些 chapter 的
        # parent_titles 里(作为它们的祖先),也视为已处理
        if leaf_node.get("expandable") and leaf_title in have_parent_titles:
            continue

        # 真缺失:按骨架位置插入
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
        # section / idx 占位,后面 _renumber_sections 统一重算
        new_chapter = _normalize_chapter(
            new_chapter,
            section="",  # 占位
            idx=len(chapters),
            parent_titles=parent_titles_list,
        )

        insert_at = _insertion_position(chapters, path, skeleton_h1_titles)
        chapters.insert(insert_at, new_chapter)
        # 更新查重集合,避免同一缺失 leaf 在同次循环里被重复插入
        have_paths.add(path)
        have_titles.add(leaf_title)
        warnings.append(f"自动补齐 fixed 叶子: {' / '.join(path)}")
        injected += 1

    if injected > 0:
        _renumber_sections(chapters)

    return chapters, warnings


def _insertion_position(
    chapters: list[dict[str, Any]],
    leaf_path: tuple[str, ...],
    skeleton_h1_titles: list[str],
) -> int:
    """决定缺失 fixed 叶子在 chapters 里的插入下标。

    - H1 级叶子(len(path)==1):在 chapters 里找最后一个 skeleton 顺序 < 它的章节,
      插在其后(连同其所有子节后)。找不到 → 0(列表开头)。
    - 子叶子(len(path)>1):在 chapters 里找父 H1 的最后一个子节,插在其后。
      父 H1 不在 chapters 里 → 退化到把父 H1 当 H1 级处理。
    """
    if not leaf_path:
        return len(chapters)

    def _chapter_h1(ch: dict[str, Any]) -> str:
        roots = ch.get("parent_titles") or []
        if roots:
            return str(roots[0])
        return str(ch.get("title") or "")

    if len(leaf_path) == 1:
        leaf_title = leaf_path[0]
        target_pos = (
            skeleton_h1_titles.index(leaf_title)
            if leaf_title in skeleton_h1_titles
            else len(skeleton_h1_titles)
        )
        last_before = -1
        for i, ch in enumerate(chapters):
            ch_h1 = _chapter_h1(ch)
            if (
                ch_h1 in skeleton_h1_titles
                and skeleton_h1_titles.index(ch_h1) < target_pos
            ):
                last_before = i
        return last_before + 1

    # 子叶子:沿父 H1 链找最后一个相关章节
    parent_h1 = leaf_path[0]
    last_under_parent = -1
    for i, ch in enumerate(chapters):
        if _chapter_h1(ch) == parent_h1:
            last_under_parent = i
    if last_under_parent >= 0:
        return last_under_parent + 1
    # 父 H1 完全不在 chapters → 按 H1 级别处理
    return _insertion_position(chapters, (parent_h1,), skeleton_h1_titles)


def _renumber_sections(chapters: list[dict[str, Any]]) -> None:
    """按 chapters 列表顺序重新派生 section 编号。

    规则:每个 chapter 的 section 由它的 ``parent_titles + [title]`` 完整路径
    决定。同一前缀第一次出现就分配下一个序号,后续保持。例:
      - ("项目建设",) 第一次出现 → section="1"
      - ("项目建设", "项目概述") 第一次 → "1.1"
      - ("项目建设", "项目概述", "背景") 第一次 → "1.1.1"
      - ("服务保障",) 第一次 → "2"

    这样不管 chapters 是怎么插入进来的,只要相对顺序对,目录就连续。
    D-EP 自动补齐后必须调一次,清掉之前的 "99.{n}" 占位。
    """
    counters: dict[tuple[str, ...], int] = {}
    assignments: dict[tuple[str, ...], int] = {}
    for ch in chapters:
        parents = tuple(str(p) for p in (ch.get("parent_titles") or []))
        title = str(ch.get("title") or "")
        full_path = (*parents, title)
        # 沿路径每一层,缺号就分配
        for depth in range(1, len(full_path) + 1):
            prefix = full_path[:depth]
            if prefix not in assignments:
                parent_prefix = prefix[:-1]
                next_idx = counters.get(parent_prefix, 0) + 1
                counters[parent_prefix] = next_idx
                assignments[prefix] = next_idx
        ch["section"] = ".".join(
            str(assignments[full_path[:depth]])
            for depth in range(1, len(full_path) + 1)
        )


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
