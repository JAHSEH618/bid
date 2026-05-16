"""Phase 2A: BlackboardIndex BM25 检索测试。"""
from __future__ import annotations

import pytest

from bid_app.services.blackboard_retrieval import BlackboardIndex, _tokenize


def test_tokenize_zh_drops_stop_words() -> None:
    tokens = _tokenize("项目经理的资质要求和经验")
    # "的" "和" 是停用词,应该被过滤
    assert "的" not in tokens
    assert "和" not in tokens
    # 实词应该保留
    assert any("项目" in t or "经理" in t for t in tokens)


def test_tokenize_handles_english_numbers() -> None:
    tokens = _tokenize("SLA 99.95% RTO 4h")
    joined = " ".join(tokens).lower()
    # 英文数字小写化保留;百分号 / 小数点是 jieba 内部切的,允许任一形式
    assert "sla" in joined
    assert "rto" in joined


def test_tokenize_empty() -> None:
    assert _tokenize("") == []
    assert _tokenize("   ") == []


def test_index_empty_entities() -> None:
    idx = BlackboardIndex(None)
    assert len(idx) == 0
    assert idx.search(query="anything") == []

    idx2 = BlackboardIndex({})
    assert len(idx2) == 0
    assert idx2.search(query="anything") == []


def test_index_basic_search() -> None:
    entities = {
        "risk_signals": [
            {"tags": ["risk_signals"], "content": "项目实施失败将扣 8 分"},
            {"tags": ["risk_signals"], "content": "缺少风险管控章节直接淘汰"},
        ],
        "technical_requirements": [
            {"tags": ["technical_requirements"], "content": "SLA 不低于 99.95% RTO 不超过 4 小时"},
            {"tags": ["technical_requirements"], "content": "支持横向扩展到 100 节点"},
        ],
        "scoring_rules": [
            {"tags": ["scoring_rules"], "content": "技术方案 50 分,商务 30 分"},
        ],
    }
    idx = BlackboardIndex(entities)
    assert len(idx) == 5

    # 找 SLA 应该命中 technical_requirements 第 1 条
    hits = idx.search(query="SLA", top_k=3)
    assert len(hits) >= 1
    assert "SLA" in hits[0]["content"]
    assert hits[0]["score"] > 0


def test_index_filter_by_entity_types() -> None:
    entities = {
        "risk_signals": [
            {"tags": ["risk_signals"], "content": "项目失败扣 8 分"},
        ],
        "technical_requirements": [
            {"tags": ["technical_requirements"], "content": "项目实施 SLA 99.95%"},
        ],
    }
    idx = BlackboardIndex(entities)
    # 限制到 risk_signals,即使 technical 里有更相关的「项目」也不出
    hits = idx.search(
        entity_types=["risk_signals"], query="项目", top_k=5
    )
    assert all(h["bucket"] == "risk_signals" for h in hits)


def test_index_multi_bucket_tags() -> None:
    """tags 里有多桶 → 任一桶在 entity_types 内都该被选中。"""
    entities = {
        "scoring_rules": [
            {
                "tags": ["scoring_rules", "risk_signals"],
                "content": "缺章节扣 8 分,严重缺失一票否决",
            }
        ]
    }
    idx = BlackboardIndex(entities)
    # 只问 risk_signals — 也应该命中(因为 tags 里有它)
    hits = idx.search(
        entity_types=["risk_signals"], query="一票否决", top_k=5
    )
    assert len(hits) == 1
    assert "risk_signals" in hits[0]["tags"]


def test_index_top_k_respected() -> None:
    entities = {
        "scoring_rules": [
            {"tags": ["scoring_rules"], "content": f"评分条款 {i}"}
            for i in range(10)
        ]
    }
    idx = BlackboardIndex(entities)
    hits = idx.search(query="评分", top_k=3)
    assert len(hits) <= 3


def test_index_no_query_returns_in_order() -> None:
    entities = {
        "project_info": [
            {"tags": ["project_info"], "content": "条目 A"},
            {"tags": ["project_info"], "content": "条目 B"},
            {"tags": ["project_info"], "content": "条目 C"},
        ]
    }
    idx = BlackboardIndex(entities)
    hits = idx.search(query="", top_k=2)
    assert len(hits) == 2
    assert hits[0]["content"] == "条目 A"
    assert hits[1]["content"] == "条目 B"


def test_index_zero_score_filtered_when_query_given() -> None:
    """有 query 但 entry 完全不沾边 → score=0 → 不出现在结果里。"""
    entities = {
        "project_info": [
            {"tags": ["project_info"], "content": "完全无关的随机内容"},
        ],
        "scoring_rules": [
            {"tags": ["scoring_rules"], "content": "SLA 必须 99.95% 以上"},
        ],
    }
    idx = BlackboardIndex(entities)
    hits = idx.search(query="SLA RTO", top_k=10)
    # project_info 那条完全没沾边,即使 top_k=10 也不该混进来
    assert all("SLA" in h["content"] or "RTO" in h["content"] for h in hits)


def test_index_preserves_metadata() -> None:
    entities = {
        "scoring_rules": [
            {
                "tags": ["scoring_rules"],
                "content": "技术 50 商务 30 资质 20",
                "source_doc": "scoring.pdf",
                "section": "§4.1",
            }
        ]
    }
    idx = BlackboardIndex(entities)
    hits = idx.search(query="技术", top_k=1)
    assert len(hits) == 1
    assert hits[0]["source_doc"] == "scoring.pdf"
    assert hits[0]["section"] == "§4.1"


@pytest.mark.parametrize(
    "entry",
    [
        {"content": ""},
        {"content": "   "},
        {},
        "not a dict",
        None,
    ],
)
def test_index_skips_invalid_entries(entry: object) -> None:
    entities = {"scoring_rules": [entry, {"content": "有效内容"}]}
    idx = BlackboardIndex(entities)  # type: ignore[arg-type]
    # 只有 1 条有效
    assert len(idx) == 1
