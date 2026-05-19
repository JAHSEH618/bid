"""D-EK: BlackboardIndex 混合召回(BM25 + 向量)测试。"""
from __future__ import annotations

from bid_app.services.blackboard_retrieval import BlackboardIndex
from bid_app.services.embeddings import EMBEDDING_DIM


def _vec(seed: float) -> list[float]:
    """造一个与 seed 对齐的简单向量,只前两维有信号,其余 0。
    便于精确控制 cosine 排序。
    """
    v = [0.0] * EMBEDDING_DIM
    v[0] = seed
    v[1] = (1.0 - seed * seed) ** 0.5 if seed * seed <= 1.0 else 0.0
    return v


def test_index_without_embeddings_pure_bm25() -> None:
    """无 embeddings 参数 → 与 Phase 2A 旧行为完全一致。"""
    entities = {
        "scoring_rules": [
            {"content": "技术 50 分商务 30 分"},
            {"content": "完全不沾边的随机内容"},
        ]
    }
    idx = BlackboardIndex(entities)
    assert idx.has_embeddings() is False
    hits = idx.search(query="技术", top_k=5)
    assert len(hits) == 1
    assert "技术" in hits[0]["content"]
    assert hits[0]["retrieval_method"] == "bm25"


def test_index_with_embeddings_but_no_query_embedding_falls_back_bm25() -> None:
    """带 embeddings 但 query_embedding=None → 只走 BM25。"""
    entities = {"scoring_rules": [{"content": "技术 50 分"}, {"content": "其他内容"}]}
    embeddings = {"scoring_rules": [_vec(1.0), _vec(0.0)]}
    idx = BlackboardIndex(entities, embeddings=embeddings)
    assert idx.has_embeddings() is True
    hits = idx.search(query="技术", top_k=5)
    assert len(hits) == 1
    assert hits[0]["retrieval_method"] == "bm25"


def test_index_hybrid_recalls_semantic_match_bm25_misses() -> None:
    """向量召回应该补回 BM25 漏的语义相近条目。

    构造:entry A 的 content 与 query 无 token 重叠,但向量高度相似(cosine ~1)。
    Pure BM25 召回不到 A,混合召回应该把它捞回来。
    """
    entities = {
        "scoring_rules": [
            {"content": "Alpha Beta Gamma"},  # 与 query 完全不重叠
            {"content": "完全无关的随机汉字"},
        ]
    }
    # entry 0 向量与 query 向量高度相似(both seed=1.0);entry 1 正交
    embeddings = {"scoring_rules": [_vec(1.0), _vec(0.0)]}
    idx = BlackboardIndex(entities, embeddings=embeddings)

    # 查询 token 跟两条 entry 都没重叠 → BM25 召回 0 条
    bm25_only = idx.search(query="xyzzy nonsense", top_k=5)
    assert bm25_only == []

    # 但向量与 entry 0 高度相似 → 混合召回应当能拿到 entry 0
    hybrid = idx.search(
        query="xyzzy nonsense",
        top_k=5,
        query_embedding=_vec(1.0),
    )
    assert len(hybrid) >= 1
    contents = [h["content"] for h in hybrid]
    assert "Alpha Beta Gamma" in contents
    # vec-only 命中标记
    alpha_hit = next(h for h in hybrid if h["content"] == "Alpha Beta Gamma")
    assert alpha_hit["retrieval_method"] in {"vec", "bm25+vec"}


def test_index_hybrid_both_methods_rank_higher() -> None:
    """两路都命中的条目应该排在仅一路命中的之前。"""
    entities = {
        "scoring_rules": [
            {"content": "技术方案 50 分"},  # BM25 命中 + 向量命中
            {"content": "Beta semantic-only"},  # 仅向量命中
        ]
    }
    embeddings = {"scoring_rules": [_vec(1.0), _vec(1.0)]}
    idx = BlackboardIndex(entities, embeddings=embeddings)

    hits = idx.search(
        query="技术",
        top_k=5,
        query_embedding=_vec(1.0),
    )
    assert len(hits) >= 1
    assert hits[0]["content"] == "技术方案 50 分"
    assert hits[0]["retrieval_method"] == "bm25+vec"


def test_index_embeddings_mismatched_length_silently_drops_vec() -> None:
    """桶下向量条数与 entry 条数对不上 → 该桶丢弃向量,退化 BM25。"""
    entities = {"scoring_rules": [{"content": "A"}, {"content": "B"}, {"content": "C"}]}
    embeddings = {"scoring_rules": [_vec(1.0), _vec(0.5)]}  # 少一条
    idx = BlackboardIndex(entities, embeddings=embeddings)
    assert idx.has_embeddings() is False


def test_index_zero_query_embedding_falls_back_bm25() -> None:
    """query_embedding 是全零(embedding 失败回退) → 不走向量分支。"""
    entities = {"scoring_rules": [{"content": "技术"}]}
    embeddings = {"scoring_rules": [_vec(1.0)]}
    idx = BlackboardIndex(entities, embeddings=embeddings)
    zero = [0.0] * EMBEDDING_DIM
    hits = idx.search(query="技术", top_k=5, query_embedding=zero)
    # 仅走 BM25
    assert all(h["retrieval_method"] == "bm25" for h in hits)


def test_index_filter_by_entity_types_still_works_in_hybrid() -> None:
    entities = {
        "scoring_rules": [{"content": "scoring entry"}],
        "risk_signals": [{"content": "risk entry"}],
    }
    embeddings = {
        "scoring_rules": [_vec(1.0)],
        "risk_signals": [_vec(1.0)],
    }
    idx = BlackboardIndex(entities, embeddings=embeddings)
    hits = idx.search(
        entity_types=["scoring_rules"],
        query="entry",
        top_k=5,
        query_embedding=_vec(1.0),
    )
    assert all(h["bucket"] == "scoring_rules" for h in hits)
