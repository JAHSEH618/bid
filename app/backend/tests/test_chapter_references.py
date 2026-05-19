"""D-EL: 参考来源采集测试(references_out + tool collector + 去重)。"""
from __future__ import annotations

from typing import Any

import pytest

from bid_app.services.blackboard_retrieval import make_blackboard_tool_handler
from bid_app.workflow.nodes.write_chapter import _dedupe_references
from bid_app.workflow.prompts.write_chapter_prompt import build_messages


def test_build_messages_populates_references_out() -> None:
    """有 entities 时 build_messages 应该把首轮召回写到 references_out。"""
    entities = {
        "scoring_rules": [
            {
                "content": "技术 50 分商务 30 分",
                "source_doc": "scoring.pdf",
                "section": "§4.1",
            }
        ],
        "technical_requirements": [{"content": "SLA 99.95%"}],
    }
    collected: list[dict[str, Any]] = []
    messages = build_messages(
        chapter={
            "section": "3.2",
            "title": "技术方案概述",
            "chapter_type": "normal",
            "key_points": ["技术"],
            "target_pages": 3,
        },
        tech_spec_md="",
        scoring_md="",
        blackboard_entities=entities,
        references_out=collected,
    )
    assert messages
    assert len(collected) >= 1
    # 命中条目应该带 bucket / content,可能带 source_doc / section
    for ref in collected:
        assert "bucket" in ref
        assert "content" in ref
        assert "retrieval_method" in ref


def test_build_messages_references_out_none_does_not_crash() -> None:
    """references_out=None 时不应该报错(向后兼容)。"""
    entities = {"scoring_rules": [{"content": "X"}]}
    messages = build_messages(
        chapter={"section": "1", "title": "T", "chapter_type": "normal", "target_pages": 1},
        tech_spec_md="",
        scoring_md="",
        blackboard_entities=entities,
        references_out=None,
    )
    assert messages


@pytest.mark.asyncio
async def test_tool_handler_collector_appends_each_call() -> None:
    """make_blackboard_tool_handler 加 collector → tool 调用时累计 hits。"""
    entities = {
        "scoring_rules": [
            {"content": "技术 50 分", "source_doc": "scoring.pdf", "section": "§4.1"}
        ]
    }
    collector: list[dict[str, Any]] = []
    handler = make_blackboard_tool_handler(entities, collector=collector)
    result = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "技术", "top_k": 5},
    )
    assert "技术" in result
    assert len(collector) == 1
    assert collector[0]["retrieval_method"] == "tool"
    assert collector[0]["source_doc"] == "scoring.pdf"
    assert collector[0]["section"] == "§4.1"

    # 再调一次 → collector 累计
    await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "技术"},
    )
    assert len(collector) == 2


@pytest.mark.asyncio
async def test_tool_handler_no_collector_works() -> None:
    """无 collector 参数 → 与旧行为一致,不报错。"""
    entities = {"scoring_rules": [{"content": "X"}]}
    handler = make_blackboard_tool_handler(entities)
    result = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "x"},
    )
    assert "hits" in result


def test_dedupe_merges_same_content_two_methods() -> None:
    """同一 content 两条来源 → 合并标 bm25+vec。"""
    items = [
        {"content": "X", "retrieval_method": "bm25", "bucket": "scoring_rules"},
        {"content": "X", "retrieval_method": "vec", "bucket": "scoring_rules"},
    ]
    out = _dedupe_references(items)
    assert len(out) == 1
    assert out[0]["retrieval_method"] == "bm25+vec"


def test_dedupe_tool_wins_over_others() -> None:
    """tool 召回最 informative,合并时优先保留。"""
    items = [
        {"content": "X", "retrieval_method": "bm25"},
        {"content": "X", "retrieval_method": "tool"},
    ]
    out = _dedupe_references(items)
    assert len(out) == 1
    assert out[0]["retrieval_method"] == "tool"


def test_dedupe_empty_input() -> None:
    assert _dedupe_references([]) == []


def test_dedupe_drops_empty_content() -> None:
    items = [
        {"content": "", "retrieval_method": "bm25"},
        {"content": "   ", "retrieval_method": "bm25"},
        {"content": "X", "retrieval_method": "bm25"},
    ]
    out = _dedupe_references(items)
    assert len(out) == 1
    assert out[0]["content"] == "X"
