"""D-EP: 自动补齐 fixed 叶子按骨架位置插入,目录不再断号。

针对 2026-05-19 生产 bug:LLM-1 漏写 评审索引表 / 偏离表 等 7 个 fixed 叶子,
旧实现统统用 ``99.{n}`` 塞末尾,目录从 6 直接跳 99,体验不好。
新实现按骨架顺序插入并重排 section,1, 2, 3, ... 连续。
"""
from __future__ import annotations

from bid_app.workflow.nodes.parse_outline import (
    _insertion_position,
    _renumber_sections,
)


def test_renumber_sections_continuous() -> None:
    """按 chapters 列表顺序重新派生 section 编号,目录连续。"""
    chapters = [
        {"title": "评审索引表", "parent_titles": []},
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "系统架构", "parent_titles": ["项目建设"]},
        {"title": "服务保障方案", "parent_titles": []},
        {"title": "项目管理", "parent_titles": ["服务保障方案"]},
    ]
    _renumber_sections(chapters)
    assert chapters[0]["section"] == "1"  # 评审索引表
    assert chapters[1]["section"] == "2.1"  # 项目建设(隐式 H1=2)/ 项目概述
    assert chapters[2]["section"] == "2.2"  # 项目建设 / 系统架构
    assert chapters[3]["section"] == "3"  # 服务保障方案
    assert chapters[4]["section"] == "3.1"


def test_renumber_handles_deep_nesting() -> None:
    """三级嵌套:1.1.1 / 1.1.2 / 1.2.1 都对。"""
    chapters = [
        {"title": "A", "parent_titles": ["X", "Y"]},
        {"title": "B", "parent_titles": ["X", "Y"]},
        {"title": "C", "parent_titles": ["X", "Z"]},
    ]
    _renumber_sections(chapters)
    assert chapters[0]["section"] == "1.1.1"
    assert chapters[1]["section"] == "1.1.2"
    assert chapters[2]["section"] == "1.2.1"


def test_renumber_resets_counter_for_new_h1() -> None:
    """新 H1 出现时,二级计数从 1 重新开始。"""
    chapters = [
        {"title": "A1", "parent_titles": ["H1"]},
        {"title": "A2", "parent_titles": ["H1"]},
        {"title": "B1", "parent_titles": ["H2"]},
    ]
    _renumber_sections(chapters)
    assert chapters[0]["section"] == "1.1"
    assert chapters[1]["section"] == "1.2"
    assert chapters[2]["section"] == "2.1"


def test_insertion_position_h1_before_first() -> None:
    """H1 级缺失叶子 + 骨架顺序在所有现有章节之前 → 插到 0。"""
    skeleton_h1 = ["评审索引表", "项目建设", "服务保障"]
    chapters = [
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "服务保障方案", "parent_titles": []},
    ]
    pos = _insertion_position(chapters, ("评审索引表",), skeleton_h1)
    assert pos == 0


def test_insertion_position_h1_at_end() -> None:
    """H1 级缺失叶子 + 骨架顺序在所有现有章节之后 → 插到末尾。"""
    skeleton_h1 = ["项目建设", "服务保障", "偏离表"]
    chapters = [
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "服务保障", "parent_titles": []},
    ]
    pos = _insertion_position(chapters, ("偏离表",), skeleton_h1)
    assert pos == 2  # 末尾


def test_insertion_position_h1_in_middle() -> None:
    """H1 级缺失叶子 + 骨架顺序在中间 → 插到对应 H1 块之后。"""
    skeleton_h1 = ["项目建设", "类似业绩", "企业实力"]
    chapters = [
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "系统架构", "parent_titles": ["项目建设"]},
        {"title": "成员证书", "parent_titles": ["企业实力"]},
    ]
    pos = _insertion_position(chapters, ("类似业绩",), skeleton_h1)
    # 应该插在「项目建设」最后一个子节后(idx=2),即 idx=2
    assert pos == 2


def test_insertion_position_sub_leaf_after_last_sibling() -> None:
    """子叶子(parent_titles 非空)→ 插在父 H1 的最后一个子节后。"""
    skeleton_h1 = ["项目建设", "企业实力"]
    chapters = [
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "公司介绍", "parent_titles": ["企业实力"]},
        {"title": "CMMI 证书", "parent_titles": ["企业实力"]},
    ]
    # 在「企业实力」下添加「ISO 证书」 → 应在 idx=3
    pos = _insertion_position(
        chapters, ("企业实力", "ISO 证书"), skeleton_h1
    )
    assert pos == 3


def test_renumber_after_real_world_injection() -> None:
    """端到端:模拟 LLM 漏写「评审索引表」+「偏离表」,补齐后目录从 1 连号。"""
    chapters = [
        {"title": "项目概述", "parent_titles": ["项目建设"]},
        {"title": "服务保障", "parent_titles": []},
        {"title": "项目管理", "parent_titles": ["服务保障"]},
        {"title": "企业实力", "parent_titles": []},
    ]
    skeleton_h1 = ["评审索引表", "项目建设", "服务保障", "企业实力", "偏离表"]

    # 1) 插「评审索引表」
    pos = _insertion_position(chapters, ("评审索引表",), skeleton_h1)
    chapters.insert(pos, {"title": "评审索引表", "parent_titles": []})

    # 2) 插「偏离表」
    pos = _insertion_position(chapters, ("偏离表",), skeleton_h1)
    chapters.insert(pos, {"title": "偏离表", "parent_titles": []})

    _renumber_sections(chapters)

    sections = [c["section"] for c in chapters]
    # 评审索引表=1, 项目概述=2.1(项目建设隐式 H1=2), 服务保障=3, 项目管理=3.1,
    # 企业实力=4, 偏离表=5
    assert sections == ["1", "2.1", "3", "3.1", "4", "5"]
    # 没有 99.x
    assert not any(s.startswith("99") for s in sections)
