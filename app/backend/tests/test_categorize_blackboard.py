"""Phase 1A: categorize_blackboard 节点 + prompt normalize 单元测试。

不接 LLM(慢 + 烧 token),只测:
- ENTITY_BUCKETS 是 10 个固定桶
- normalize_entities 处理多种异常 LLM 输出
- 节点空 excerpt 兜底返回 10 桶 [] 而不是抛
"""

from __future__ import annotations

import pytest

from bid_app.workflow.prompts.categorize_blackboard import (
    ENTITY_BUCKETS,
    normalize_entities,
)


def test_ten_fixed_buckets() -> None:
    assert len(ENTITY_BUCKETS) == 10
    assert "project_info" in ENTITY_BUCKETS
    assert "risk_signals" in ENTITY_BUCKETS


def test_normalize_full_valid() -> None:
    raw = {
        "project_info": [
            {
                "tags": ["project_info"],
                "content": "招标方为 __ORG_001__,项目编号 __PROJ_001__",
                "source_doc": "tech_spec.docx",
                "section": "§1.1",
            }
        ],
        "risk_signals": [
            {"tags": ["risk_signals", "compliance_constraints"], "content": "缺章节扣 8 分"}
        ],
    }
    out = normalize_entities(raw)
    assert set(out.keys()) == set(ENTITY_BUCKETS)
    assert len(out["project_info"]) == 1
    assert out["project_info"][0]["source_doc"] == "tech_spec.docx"
    assert out["risk_signals"][0]["tags"] == ["risk_signals", "compliance_constraints"]


def test_normalize_drops_invalid_buckets() -> None:
    """LLM 自创桶名(如 hr_info)直接丢,不入结果。"""
    raw = {
        "hr_info": [{"content": "项目经理需 10 年经验"}],
        "personnel_info": [{"content": "项目经理需 10 年经验"}],
    }
    out = normalize_entities(raw)
    assert "hr_info" not in out
    assert len(out["personnel_info"]) == 1


def test_normalize_drops_invalid_tags() -> None:
    """tags 里不在 10 桶名单的字符串过滤掉,空了兜底用 bucket 名。"""
    raw = {
        "scoring_rules": [
            {"tags": ["scoring_rules", "made_up"], "content": "权重 30%"},
            {"tags": ["fake1", "fake2"], "content": "技术分占 50"},
        ]
    }
    out = normalize_entities(raw)
    assert out["scoring_rules"][0]["tags"] == ["scoring_rules"]
    assert out["scoring_rules"][1]["tags"] == ["scoring_rules"]


def test_normalize_drops_empty_content() -> None:
    raw = {
        "scoring_rules": [
            {"tags": ["scoring_rules"], "content": ""},
            {"tags": ["scoring_rules"], "content": "   "},
            {"tags": ["scoring_rules"], "content": "valid"},
        ]
    }
    out = normalize_entities(raw)
    assert len(out["scoring_rules"]) == 1
    assert out["scoring_rules"][0]["content"] == "valid"


def test_normalize_handles_garbage() -> None:
    """非 dict / 桶值非 list / entry 非 dict 都不应崩溃。"""
    assert normalize_entities(None) == {b: [] for b in ENTITY_BUCKETS}
    assert normalize_entities("string") == {b: [] for b in ENTITY_BUCKETS}
    assert normalize_entities(
        {"scoring_rules": "not a list"}
    ) == {b: [] for b in ENTITY_BUCKETS}
    out = normalize_entities({"scoring_rules": [None, 42, {"content": "ok"}]})
    assert len(out["scoring_rules"]) == 1


@pytest.mark.asyncio
async def test_node_empty_excerpt_returns_empty_buckets() -> None:
    """空 blackboard_excerpt 不调 LLM,直接返回 10 桶 []。"""
    from bid_app.workflow.nodes.categorize_blackboard import run

    state: dict = {
        "project_id": -1,  # CLI 路径,不动 DB
        "blackboard_excerpt": "",
    }
    result = await run(state)  # type: ignore[arg-type]
    assert "blackboard_entities" in result
    assert set(result["blackboard_entities"].keys()) == set(ENTITY_BUCKETS)
    for v in result["blackboard_entities"].values():
        assert v == []
