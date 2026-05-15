"""PR-M8-2 follow-up: parse_outline 层级 TOC 展平测试。"""
from __future__ import annotations

import asyncio
import json

from bid_app.workflow.nodes import parse_outline


def _run(outline_json: str) -> list[dict]:
    state: dict = {"_outline_json": outline_json}
    result = asyncio.run(parse_outline.run(state))
    return result["chapters"]


def test_hierarchical_toc_flattens_with_section() -> None:
    """新 schema {"toc": [...]} 走层级展平,每个叶子带 section "1.1" / "2.1"。"""
    payload = {
        "toc": [
            {
                "title": "项目背景",
                "children": [
                    {
                        "title": "招标方现状",
                        "key_points": ["规模", "痛点"],
                        "target_pages": 2,
                    },
                    {
                        "title": "项目需求",
                        "key_points": ["范围", "目标"],
                        "target_pages": 3,
                    },
                ],
            },
            {
                "title": "技术方案",
                "children": [
                    {
                        "title": "总体架构",
                        "key_points": ["分层", "组件"],
                        "target_pages": 4,
                    }
                ],
            },
        ]
    }
    chapters = _run(json.dumps(payload))
    assert len(chapters) == 3
    assert [c["section"] for c in chapters] == ["1.1", "1.2", "2.1"]
    assert [c["title"] for c in chapters] == ["招标方现状", "项目需求", "总体架构"]
    assert chapters[0]["key_points"] == ["规模", "痛点"]
    assert chapters[2]["target_pages"] == 4


def test_three_level_toc_flattens_to_leaves_only() -> None:
    """三级目录:只有最深的叶子进 chapters[];中间节点只贡献编号。"""
    payload = {
        "toc": [
            {
                "title": "技术方案",
                "children": [
                    {
                        "title": "数据层",
                        "children": [
                            {
                                "title": "数据库选型",
                                "key_points": ["PG"],
                                "target_pages": 1,
                            },
                            {
                                "title": "缓存策略",
                                "key_points": ["Redis"],
                                "target_pages": 1,
                            },
                        ],
                    },
                ],
            },
        ]
    }
    chapters = _run(json.dumps(payload))
    assert [c["section"] for c in chapters] == ["1.1.1", "1.1.2"]
    assert [c["title"] for c in chapters] == ["数据库选型", "缓存策略"]
    # textarea TOC editor:祖先标题 round-trip 用,前端 chaptersToTocText 重建分组行
    assert chapters[0]["parent_titles"] == ["技术方案", "数据层"]
    assert chapters[1]["parent_titles"] == ["技术方案", "数据层"]


def test_four_level_toc_preserves_parent_titles() -> None:
    """4 级目录的叶子保留 3 个祖先标题。"""
    payload = {
        "toc": [
            {
                "title": "项目背景",
                "children": [
                    {
                        "title": "招标方现状",
                        "children": [
                            {
                                "title": "组织架构",
                                "children": [
                                    {
                                        "title": "总部与分支",
                                        "key_points": ["x"],
                                        "target_pages": 1,
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ]
    }
    chapters = _run(json.dumps(payload))
    assert len(chapters) == 1
    assert chapters[0]["section"] == "1.1.1.1"
    assert chapters[0]["parent_titles"] == ["项目背景", "招标方现状", "组织架构"]


def test_legacy_flat_chapters_still_parsed_with_fallback_section() -> None:
    """向后兼容:LLM-1 仍出 v1 {"chapters": [...]} 时,section 按 idx+1 兜底。"""
    payload = {
        "chapters": [
            {"title": "概述", "key_points": ["x"], "target_pages": 2},
            {"title": "实施", "key_points": ["y"], "target_pages": 3},
        ]
    }
    chapters = _run(json.dumps(payload))
    assert [c["section"] for c in chapters] == ["1", "2"]
    assert [c["title"] for c in chapters] == ["概述", "实施"]


def test_empty_toc_returns_empty_chapters() -> None:
    assert _run('{"toc": []}') == []
    assert _run("not json at all") == []


def test_markdown_code_fence_is_stripped() -> None:
    payload = '```json\n{"toc": [{"title": "X", "children": [{"title": "X.1"}]}]}\n```'
    chapters = _run(payload)
    assert len(chapters) == 1
    assert chapters[0]["section"] == "1.1"
    assert chapters[0]["title"] == "X.1"
