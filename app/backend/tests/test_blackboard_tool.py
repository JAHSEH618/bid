"""Phase 2B: search_blackboard tool definition + handler factory tests."""
from __future__ import annotations

import json

import pytest

from bid_app.services.blackboard_retrieval import (
    SEARCH_BLACKBOARD_TOOL,
    make_blackboard_tool_handler,
)


def test_tool_schema_has_required_fields() -> None:
    """OpenAI / DashScope 兼容性:type=function + function.name + parameters。"""
    assert SEARCH_BLACKBOARD_TOOL["type"] == "function"
    fn = SEARCH_BLACKBOARD_TOOL["function"]
    assert fn["name"] == "search_blackboard"
    assert "description" in fn
    params = fn["parameters"]
    assert params["type"] == "object"
    # entity_types 是必需,在 enum 内
    assert "entity_types" in params["required"]
    enum = params["properties"]["entity_types"]["items"]["enum"]
    # 与 categorize_blackboard.ENTITY_BUCKETS 同步(10 桶)
    assert len(enum) == 10
    assert "scoring_rules" in enum
    assert "risk_signals" in enum


@pytest.mark.asyncio
async def test_handler_basic_search() -> None:
    entities = {
        "risk_signals": [{"content": "缺章节扣 8 分"}],
        "technical_requirements": [{"content": "SLA 99.95% RTO 4h"}],
    }
    handler = make_blackboard_tool_handler(entities)
    # query 用一个真会被 jieba 切出来的关键字
    result_str = await handler(
        "search_blackboard",
        {"entity_types": ["risk_signals"], "query": "章节"},
    )
    payload = json.loads(result_str)
    assert "hits" in payload
    assert payload["count"] >= 1
    assert any("章节" in h["content"] for h in payload["hits"])


@pytest.mark.asyncio
async def test_handler_string_entity_types_coerced() -> None:
    """模型偶尔把 entity_types 作为字符串发回 → 自动包成单元素 list。"""
    entities = {"scoring_rules": [{"content": "技术 50 商务 30"}]}
    handler = make_blackboard_tool_handler(entities)
    result = await handler(
        "search_blackboard",
        {"entity_types": "scoring_rules", "query": "技术"},
    )
    payload = json.loads(result)
    assert payload["count"] >= 1


@pytest.mark.asyncio
async def test_handler_invalid_entity_types_returns_error() -> None:
    entities = {"scoring_rules": [{"content": "test"}]}
    handler = make_blackboard_tool_handler(entities)
    result = await handler(
        "search_blackboard",
        {"entity_types": 42, "query": "x"},  # 既不是 list 也不是 str
    )
    payload = json.loads(result)
    assert "error" in payload


@pytest.mark.asyncio
async def test_handler_unknown_tool_name_returns_error() -> None:
    handler = make_blackboard_tool_handler({})
    result = await handler("delete_universe", {})
    payload = json.loads(result)
    assert "error" in payload
    assert "delete_universe" in payload["error"]


@pytest.mark.asyncio
async def test_handler_empty_entities_returns_empty_hits() -> None:
    handler = make_blackboard_tool_handler(None)
    result = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "anything"},
    )
    payload = json.loads(result)
    assert payload["count"] == 0
    assert payload["hits"] == []


@pytest.mark.asyncio
async def test_handler_top_k_clamped() -> None:
    """top_k 越界 → clamp 到 [1, 20]。"""
    entities = {
        "scoring_rules": [
            {"content": f"条目 {i}"} for i in range(30)
        ]
    }
    handler = make_blackboard_tool_handler(entities)

    # top_k=999 → clamp 20
    r1 = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "", "top_k": 999},
    )
    assert json.loads(r1)["count"] <= 20

    # top_k=0 → clamp 1
    r2 = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "", "top_k": 0},
    )
    assert json.loads(r2)["count"] == 1

    # top_k="not a number" → 默认 5
    r3 = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "", "top_k": "x"},
    )
    assert json.loads(r3)["count"] == 5


@pytest.mark.asyncio
async def test_handler_preserves_source_metadata() -> None:
    entities = {
        "scoring_rules": [
            {
                "content": "技术 50",
                "source_doc": "scoring.pdf",
                "section": "§4.1",
            }
        ]
    }
    handler = make_blackboard_tool_handler(entities)
    result = await handler(
        "search_blackboard",
        {"entity_types": ["scoring_rules"], "query": "技术"},
    )
    hit = json.loads(result)["hits"][0]
    assert hit["source_doc"] == "scoring.pdf"
    assert hit["section"] == "§4.1"
    # score 不传给 LLM(无用)
    assert "score" not in hit
