"""D-EF 模版骨架测试。"""
from __future__ import annotations

import asyncio
import json

from bid_app.workflow.nodes import parse_outline
from bid_app.workflow.prompts import outline_prompt
from bid_app.workflow.templates import (
    DEFAULT_PACK_ID,
    build_title_path_index,
    fixed_leaf_paths,
    iter_skeleton_leaves,
    load_pack,
    pick_pack,
)


def test_default_pack_loads() -> None:
    pack = load_pack(DEFAULT_PACK_ID)
    assert pack["id"] == DEFAULT_PACK_ID
    assert "skeleton" in pack
    assert isinstance(pack["skeleton"], list)
    # 9 个 H1(评审索引/评审细项/项目方案/服务保障/系统安全/类似业绩/企业实力/人员/偏离表)
    assert len(pack["skeleton"]) == 9


def test_pick_pack_falls_back_for_unknown_category() -> None:
    pack = pick_pack("unknown_category_xxx")
    assert pack["id"] == DEFAULT_PACK_ID
    # None 也回落
    assert pick_pack(None)["id"] == DEFAULT_PACK_ID


def test_skeleton_has_expected_fixed_leaves() -> None:
    pack = load_pack(DEFAULT_PACK_ID)
    fixed = fixed_leaf_paths(pack["skeleton"])
    titles = {path[-1] for path in fixed}
    # rule.md §1 / §9 的固定章
    assert "评审索引表" in titles
    assert "技术商务符合性评审细项" in titles
    assert "技术/商务响应与偏离表" in titles
    # rule.md §3 设计原则 / 架构
    assert "总体设计原则" in titles
    assert "应用架构方案" in titles
    # rule.md §7 资质 4 项
    assert "信息安全管理体系认证证书" in titles
    assert "CMMI5证书" in titles


def test_title_path_index_returns_leaves_with_chapter_type() -> None:
    pack = load_pack(DEFAULT_PACK_ID)
    idx = build_title_path_index(pack["skeleton"])
    # 任取一条 principle 章
    key = ("项目建设及技术事项方案", "系统整体架构", "总体设计原则")
    assert key in idx
    leaf = idx[key]
    assert leaf["chapter_type"] == "principle"
    assert "开放性" in leaf["required_anchors"]


def test_skeleton_block_rendered_for_prompt() -> None:
    pack = load_pack(DEFAULT_PACK_ID)
    block = outline_prompt._render_skeleton_block(pack["skeleton"])
    assert "评审索引表" in block
    assert "[F" in block  # fixed 紧凑标记
    assert "E " in block  # expandable 紧凑标记
    assert "/principle" in block  # chapter_type 紧凑标记


def test_parse_outline_overlays_chapter_type_from_skeleton() -> None:
    """LLM 漏写 chapter_type 时,parse_outline 应从骨架反查回填。"""
    payload = {
        "toc": [
            {
                "title": "项目建设及技术事项方案",
                "children": [
                    {
                        "title": "系统整体架构",
                        "children": [
                            {
                                "title": "总体设计原则",
                                "key_points": ["x"],
                                "target_pages": 2,
                            },
                        ],
                    },
                ],
            },
        ]
    }
    state: dict = {
        "_outline_json": json.dumps(payload),
        "template_pack": DEFAULT_PACK_ID,
    }
    result = asyncio.run(parse_outline.run(state))
    chapters = result["chapters"]
    assert len(chapters) == 1
    leaf = chapters[0]
    assert leaf["chapter_type"] == "principle"
    assert "开放性" in leaf["required_anchors"]
    assert leaf["template_slot"] == "design_principles"


def test_parse_outline_default_chapter_type_when_no_pack() -> None:
    """无 template_pack 时,叶子 chapter_type 默认 normal,不报错。"""
    payload = {"toc": [{"title": "X", "children": [{"title": "Y", "target_pages": 1}]}]}
    state: dict = {"_outline_json": json.dumps(payload)}
    result = asyncio.run(parse_outline.run(state))
    leaf = result["chapters"][0]
    assert leaf["chapter_type"] == "normal"
    assert leaf["required_anchors"] == []
    assert leaf["template_slot"] == ""


def test_parse_outline_skeleton_fallback_by_title_only() -> None:
    """LLM 把骨架节点挪到了不同的祖先路径,parse_outline 仍能按标题兜底匹配。"""
    payload = {
        "toc": [
            {
                "title": "随便一个一级标题",
                "children": [
                    {
                        "title": "应用架构方案",  # 骨架在「系统整体架构」下,但用标题兜底
                        "key_points": ["x"],
                        "target_pages": 2,
                    },
                ],
            },
        ]
    }
    state: dict = {
        "_outline_json": json.dumps(payload),
        "template_pack": DEFAULT_PACK_ID,
    }
    result = asyncio.run(parse_outline.run(state))
    leaf = result["chapters"][0]
    assert leaf["chapter_type"] == "architecture"
    assert "接入层" in leaf["required_anchors"]


def test_iter_skeleton_leaves_traverses_image_only_chains() -> None:
    """image_only 类章节(资质 4 项)应作为叶子被遍历到。"""
    pack = load_pack(DEFAULT_PACK_ID)
    leaves = iter_skeleton_leaves(pack["skeleton"])
    titles = [leaf.get("title") for _, leaf in leaves]
    # 业绩章作为 expandable 也算叶子(自身无 children)
    assert "类似业绩" in titles
    # 资质 4 项
    assert "CMMI5证书" in titles
    assert "质量管理体系认证证书" in titles
