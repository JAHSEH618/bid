"""实体黑板 BM25 检索 (Phase 2A, 2026-05-16)。

数据源:``Project.blackboard_entities`` 的 10 桶 JSON,categorize_blackboard
节点写入。

API:

    index = BlackboardIndex(entities)
    hits = index.search(
        entity_types=["risk_signals", "compliance_constraints"],
        query="服务可用性 RTO",
        top_k=5,
    )

每个 hit 形如 ``{"bucket": "risk_signals", "score": 0.83, "content": ...,
"tags": [...], "source_doc"?: ..., "section"?: ...}``。

构建成本:对几百条 entry,jieba 分词 + BM25 build 整体 < 50ms (起 worker
后第一次会 lazy 加载 jieba 字典约 0.5s)。Phase 2B 把它包成 LiteLLM
tool callback,LLM-1/2 按需调用。Phase 1B 也可以在静态注入路径上用它做
keyword pre-filter。
"""

from __future__ import annotations

from typing import Any

import jieba
from rank_bm25 import BM25Okapi

# 中文 stop words(常见无信息词);英文走小写化 + 简单切。
# 没必要做大词典,Phase 2A 检索的是关键字命中,精度可接受。
_ZH_STOP = {
    "的", "了", "和", "与", "或", "及", "等", "在", "是", "为", "对",
    "对于", "关于", "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "并", "及其", "其", "其中", "以", "以及", "以便", "以免", "因为",
    "所以", "如果", "但是", "然而", "另外", "此外", "即可", "即", "也",
    "亦", "都", "都是", "应", "需", "可", "将", "把", "被", "本", "本节",
    "中", "上", "下",
}


def _tokenize(text: str) -> list[str]:
    """中文 jieba + 英文小写 split,过滤 stop word + 1 字符 token。

    jieba.lcut 对纯英文/数字也能切(直接当 token 返回),不用混合策略。
    """
    if not text:
        return []
    tokens = jieba.lcut(text, cut_all=False)
    out: list[str] = []
    for t in tokens:
        s = t.strip().lower()
        if not s or s in _ZH_STOP:
            continue
        # 过滤纯标点 / 单字符停用
        if len(s) == 1 and not s.isalnum():
            continue
        out.append(s)
    return out


class BlackboardIndex:
    """单个项目的 10 桶 BM25 内存索引。

    构造时一次性把所有桶的所有 entry 平铺、分词、建索引。``search`` 时按
    ``entity_types`` 过滤候选范围,再 BM25 排序取 top_k。

    线程安全:只读;jieba 分词器全局共享,内部线程安全。
    """

    def __init__(self, entities: dict[str, Any] | None) -> None:
        self._entries: list[dict[str, Any]] = []
        self._tokens: list[list[str]] = []
        self._bm25: BM25Okapi | None = None
        if not entities:
            return
        for bucket, items in entities.items():
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                content = entry.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                # 内置 bucket 名作为 entry 的「主桶」用于 search 过滤;
                # entry.tags 里可能有更多桶(多桶归属),保留以供调用方过滤
                tags = entry.get("tags") or [bucket]
                if not isinstance(tags, list):
                    tags = [bucket]
                tags_clean = [str(t) for t in tags if isinstance(t, str)]
                if bucket not in tags_clean:
                    tags_clean.append(bucket)
                stored: dict[str, Any] = {
                    "bucket": bucket,
                    "tags": tags_clean,
                    "content": content.strip(),
                }
                source = entry.get("source_doc")
                section = entry.get("section")
                if isinstance(source, str) and source.strip():
                    stored["source_doc"] = source.strip()
                if isinstance(section, str) and section.strip():
                    stored["section"] = section.strip()
                self._entries.append(stored)
                self._tokens.append(_tokenize(content))
        if self._tokens:
            # rank_bm25 不接受空文档语料;空时 _bm25 留 None,search 直接返 []
            self._bm25 = BM25Okapi(self._tokens)

    def __len__(self) -> int:
        return len(self._entries)

    def search(
        self,
        *,
        entity_types: list[str] | None = None,
        query: str = "",
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """按桶过滤 + BM25 排序。

        - ``entity_types`` 空 / None → 全 10 桶都候选
        - ``query`` 空 → 不算 BM25,只按 entity_types 过滤后取前 top_k
          (按 entry 原顺序,通常 LLM-0 已按重要性排好了)
        - 返回 list 每项含 ``bucket`` / ``tags`` / ``content`` / ``score`` /
          可选 ``source_doc`` / ``section``
        """
        if not self._entries:
            return []

        # 第一步:按 bucket 过滤候选 index
        allowed = set(entity_types or [])
        candidate_indices: list[int]
        if allowed:
            candidate_indices = [
                i
                for i, e in enumerate(self._entries)
                if any(t in allowed for t in e["tags"])
            ]
        else:
            candidate_indices = list(range(len(self._entries)))

        if not candidate_indices:
            return []

        # 第二步:无 query → 按原顺序前 top_k
        q = (query or "").strip()
        if not q:
            results: list[dict[str, Any]] = []
            for idx in candidate_indices[:top_k]:
                hit = dict(self._entries[idx])
                hit["score"] = 0.0
                results.append(hit)
            return results

        # 第三步:有 query → BM25 算全量分数,然后只在 candidates 里选
        if self._bm25 is None:
            return []
        query_tokens = _tokenize(q)
        if not query_tokens:
            # query 分词后全是 stop word — 退化到无 query 模式
            results = []
            for idx in candidate_indices[:top_k]:
                hit = dict(self._entries[idx])
                hit["score"] = 0.0
                results.append(hit)
            return results

        all_scores = self._bm25.get_scores(query_tokens)
        # 命中判定:query token 与 entry token 至少有一个交集。
        # 不能直接用 score>0:rank_bm25 在小语料(1-2 docs)上 IDF 会变负,
        # 即使 query 在 entry 里出现也可能拿到 ≤0 的分,简单 score>0 过滤
        # 会把这些真命中误杀。
        query_token_set = set(query_tokens)
        relevant_indices = [
            i
            for i in candidate_indices
            if query_token_set & set(self._tokens[i])
        ]
        if not relevant_indices:
            return []
        ranked = sorted(
            relevant_indices,
            key=lambda i: float(all_scores[i]),
            reverse=True,
        )
        out: list[dict[str, Any]] = []
        for idx in ranked[:top_k]:
            score = float(all_scores[idx])
            hit = dict(self._entries[idx])
            hit["score"] = round(score, 4)
            out.append(hit)
        return out


def build_index(entities: dict[str, Any] | None) -> BlackboardIndex:
    """便捷构造函数。"""
    return BlackboardIndex(entities)
