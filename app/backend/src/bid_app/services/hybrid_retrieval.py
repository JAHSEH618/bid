"""RRF(Reciprocal Rank Fusion)融合算法(D-EK,2026-05-19)。

把 BM25 排名和向量相似度排名按 RRF 公式融合,避免归一化两套量纲不同的
分数。公式:``score(d) = sum( 1 / (k + rank_i(d)) )``,k 默认 60。

为什么 RRF 不是加权和?
- BM25 score 无上界(IDF * tf 可任意大);cosine 在 [-1, 1]
- 加权前要归一化,但 BM25 的分布因语料而异(短文档库 vs 长文档库分数差几个数量级)
- RRF 只看排名,k=60 是论文经验值,抗分布偏移 — 实测效果通常优于加权和

参考:Cormack et al. (2009) Reciprocal Rank Fusion outperforms Condorcet
and individual rank learning methods.
"""

from __future__ import annotations

from typing import Any

DEFAULT_RRF_K = 60


def _entry_key(hit: dict[str, Any]) -> str:
    """用 content 做唯一键(同一条目两路召回会指向相同 content)。"""
    return hit.get("content") or ""


def rrf_fuse(
    bm25_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int = 12,
) -> list[dict[str, Any]]:
    """融合两路召回 → 按 RRF 分数排序 → 取 top_k。

    返回的 hit dict 继承原 hit 字段(优先取 bm25 那份,保留 source_doc / section
    等元信息),加 ``retrieval_method`` 字段:
    - 仅 BM25 命中 → ``"bm25"``
    - 仅向量命中 → ``"vec"``
    - 两路都命中 → ``"bm25+vec"``

    ``score`` 字段覆盖为 RRF 融合分(便于 caller 调试 / 排序展示)。
    """
    if not bm25_hits and not vec_hits:
        return []

    # rank_i 是 1-based,排名越小贡献越大
    bm25_rank: dict[str, int] = {_entry_key(h): i + 1 for i, h in enumerate(bm25_hits)}
    vec_rank: dict[str, int] = {_entry_key(h): i + 1 for i, h in enumerate(vec_hits)}

    keys = set(bm25_rank) | set(vec_rank)
    keys.discard("")  # 防止空 content 进结果

    scored: list[tuple[float, str]] = []
    for key in keys:
        score = 0.0
        if key in bm25_rank:
            score += 1.0 / (k + bm25_rank[key])
        if key in vec_rank:
            score += 1.0 / (k + vec_rank[key])
        scored.append((score, key))

    scored.sort(key=lambda t: t[0], reverse=True)

    # 用 content 反查 hit dict(优先 bm25 的,因为 BlackboardIndex 直接构造,字段全)
    by_content: dict[str, dict[str, Any]] = {}
    for h in vec_hits:
        by_content[_entry_key(h)] = h
    for h in bm25_hits:
        by_content[_entry_key(h)] = h  # bm25 覆盖,优先级更高

    out: list[dict[str, Any]] = []
    for score, key in scored[:top_k]:
        base = dict(by_content[key])
        in_bm25 = key in bm25_rank
        in_vec = key in vec_rank
        if in_bm25 and in_vec:
            base["retrieval_method"] = "bm25+vec"
        elif in_bm25:
            base["retrieval_method"] = "bm25"
        else:
            base["retrieval_method"] = "vec"
        base["score"] = round(score, 6)
        out.append(base)
    return out


__all__ = ["DEFAULT_RRF_K", "rrf_fuse"]
