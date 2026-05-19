"""D-EK: RRF (Reciprocal Rank Fusion) 单元测试。"""
from __future__ import annotations

from bid_app.services.hybrid_retrieval import DEFAULT_RRF_K, rrf_fuse


def _hit(content: str, **extra: object) -> dict[str, object]:
    return {"content": content, "bucket": "scoring_rules", **extra}


def test_rrf_empty_both_returns_empty() -> None:
    assert rrf_fuse([], []) == []


def test_rrf_only_bm25_falls_through() -> None:
    bm25 = [_hit("A"), _hit("B"), _hit("C")]
    out = rrf_fuse(bm25, [], top_k=10)
    assert [h["content"] for h in out] == ["A", "B", "C"]
    assert all(h["retrieval_method"] == "bm25" for h in out)


def test_rrf_only_vec_marks_method() -> None:
    vec = [_hit("X"), _hit("Y")]
    out = rrf_fuse([], vec, top_k=10)
    assert [h["content"] for h in out] == ["X", "Y"]
    assert all(h["retrieval_method"] == "vec" for h in out)


def test_rrf_combined_method_for_intersection() -> None:
    # A 两路都命中 → 应该排第一,标 bm25+vec
    bm25 = [_hit("A"), _hit("B"), _hit("C")]
    vec = [_hit("A"), _hit("X")]
    out = rrf_fuse(bm25, vec, top_k=4)
    assert out[0]["content"] == "A"
    assert out[0]["retrieval_method"] == "bm25+vec"
    # 其余条目按融合分排,vec X(只一路、排第二)与 bm25 B / C(只一路)比
    others = [(h["content"], h["retrieval_method"]) for h in out[1:]]
    contents = [c for c, _ in others]
    assert "B" in contents
    assert "C" in contents
    assert "X" in contents


def test_rrf_score_formula() -> None:
    # k=60, A 仅 BM25 rank 1 = 1/61 = 0.016393...
    # B 仅 VEC rank 1 = 1/61
    bm25 = [_hit("A")]
    vec = [_hit("B")]
    out = rrf_fuse(bm25, vec, top_k=2, k=DEFAULT_RRF_K)
    scores = {h["content"]: h["score"] for h in out}
    assert abs(scores["A"] - 1 / 61) < 1e-6
    assert abs(scores["B"] - 1 / 61) < 1e-6


def test_rrf_score_ordering_favors_double_hit() -> None:
    # A 两路 rank 1 → score = 2/(60+1)
    # B 仅 BM25 rank 1 → score = 1/(60+1)
    # A 必须排在 B 之前
    bm25 = [_hit("A"), _hit("B")]
    vec = [_hit("A")]
    out = rrf_fuse(bm25, vec, top_k=2)
    assert out[0]["content"] == "A"
    assert out[1]["content"] == "B"
    assert out[0]["score"] > out[1]["score"]


def test_rrf_top_k_truncation() -> None:
    bm25 = [_hit(c) for c in "ABCDEFG"]
    out = rrf_fuse(bm25, [], top_k=3)
    assert len(out) == 3


def test_rrf_preserves_metadata() -> None:
    bm25 = [_hit("A", source_doc="scoring.pdf", section="§4.1", bucket="scoring_rules")]
    out = rrf_fuse(bm25, [], top_k=1)
    assert out[0]["source_doc"] == "scoring.pdf"
    assert out[0]["section"] == "§4.1"
    assert out[0]["bucket"] == "scoring_rules"


def test_rrf_empty_content_filtered() -> None:
    bm25 = [{"content": "", "bucket": "x"}, _hit("A")]
    out = rrf_fuse(bm25, [], top_k=5)
    assert len(out) == 1
    assert out[0]["content"] == "A"
