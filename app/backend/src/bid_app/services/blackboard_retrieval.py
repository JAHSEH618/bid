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

from .embeddings import EMBEDDING_DIM, cosine_similarity
from .hybrid_retrieval import rrf_fuse

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

    def __init__(
        self,
        entities: dict[str, Any] | None,
        embeddings: dict[str, list[list[float]]] | None = None,
    ) -> None:
        """构造索引。

        ``embeddings`` 形状与 ``entities`` 对齐:同桶下第 i 条 entry 对应
        ``embeddings[bucket][i]``。条目数不匹配时该桶向量忽略,降级纯 BM25。
        D-EK 混合召回时由 categorize_blackboard 节点预先算好传入。
        """
        self._entries: list[dict[str, Any]] = []
        self._tokens: list[list[str]] = []
        self._embeddings: list[list[float]] = []
        self._bm25: BM25Okapi | None = None
        if not entities:
            return
        for bucket, items in entities.items():
            if not isinstance(items, list):
                continue
            bucket_embs = (embeddings or {}).get(bucket) if embeddings else None
            # 桶级向量数与 entries 数对不上 → 该桶丢弃向量,后续 search 退化纯 BM25
            if bucket_embs is not None and len(bucket_embs) != len(items):
                bucket_embs = None
            for i, entry in enumerate(items):
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
                if bucket_embs is not None:
                    emb = bucket_embs[i]
                    if isinstance(emb, list) and len(emb) == EMBEDDING_DIM:
                        self._embeddings.append([float(x) for x in emb])
                    else:
                        self._embeddings.append([])
                else:
                    self._embeddings.append([])
        if self._tokens:
            # rank_bm25 不接受空文档语料;空时 _bm25 留 None,search 直接返 []
            self._bm25 = BM25Okapi(self._tokens)

    def __len__(self) -> int:
        return len(self._entries)

    def has_embeddings(self) -> bool:
        """是否至少有一条 entry 带非空 embedding(用于决定是否走混合召回)。"""
        return any(len(e) == EMBEDDING_DIM for e in self._embeddings)

    def search(
        self,
        *,
        entity_types: list[str] | None = None,
        query: str = "",
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """按桶过滤 + BM25 / 混合排序。

        - ``entity_types`` 空 / None → 全 10 桶都候选
        - ``query`` 空 → 不算 BM25,只按 entity_types 过滤后取前 top_k
          (按 entry 原顺序,通常 LLM-0 已按重要性排好了)
        - ``query_embedding`` 非空且索引带 embeddings → 走 RRF 混合召回;
          其他情况退化纯 BM25
        - 返回 list 每项含 ``bucket`` / ``tags`` / ``content`` / ``score`` /
          可选 ``source_doc`` / ``section`` / ``retrieval_method``
        """
        bm25_hits = self._bm25_search(
            entity_types=entity_types, query=query, top_k=max(top_k * 3, top_k + 5)
        )

        use_hybrid = (
            query_embedding is not None
            and len(query_embedding) == EMBEDDING_DIM
            and any(x != 0.0 for x in query_embedding)
            and self.has_embeddings()
        )
        if not use_hybrid:
            return bm25_hits[:top_k]

        vec_hits = self._vec_search(
            entity_types=entity_types,
            query_embedding=query_embedding or [],
            top_k=max(top_k * 3, top_k + 5),
        )
        fused = rrf_fuse(bm25_hits, vec_hits, top_k=top_k)
        return fused

    def _bm25_search(
        self,
        *,
        entity_types: list[str] | None,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """原 BM25 路径,提取为私有方法以便复用。"""
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
                hit["retrieval_method"] = "bm25"
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
                hit["retrieval_method"] = "bm25"
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
            hit["retrieval_method"] = "bm25"
            out.append(hit)
        return out

    def _vec_search(
        self,
        *,
        entity_types: list[str] | None,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """向量 cosine 路径。entity_types / 空 query_embedding 检查由 caller 负责。"""
        if not self._entries or not query_embedding:
            return []
        allowed = set(entity_types or [])
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
        # 没向量的位置算 0 分,自然排到尾部
        scored = [
            (cosine_similarity(query_embedding, self._embeddings[i]), i)
            for i in candidate_indices
        ]
        # 过滤 score <= 0(纯零向量 / 反相关)
        scored = [(s, i) for s, i in scored if s > 0.0]
        scored.sort(key=lambda t: t[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, idx in scored[:top_k]:
            hit = dict(self._entries[idx])
            hit["score"] = round(float(score), 4)
            hit["retrieval_method"] = "vec"
            out.append(hit)
        return out


def build_index(
    entities: dict[str, Any] | None,
    embeddings: dict[str, list[list[float]]] | None = None,
) -> BlackboardIndex:
    """便捷构造函数。"""
    return BlackboardIndex(entities, embeddings=embeddings)


# Phase 2B (2026-05-16):LiteLLM tool 定义 + handler 工厂。LLM-1 outline
# 节点把这个工具注入到 acompletion 调用,模型据此自主检索实体黑板。
# entity_types 枚举与 categorize_blackboard.ENTITY_BUCKETS 一致;改这里要回头同步。

SEARCH_BLACKBOARD_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_blackboard",
        "description": (
            "从招标项目的实体黑板里检索相关条目。当你需要查找招标方信息、"
            "评分细则、技术要求、人员资质、风险条款等具体内容时主动调用。"
            "返回 list,每条带 bucket / content / source_doc / section。"
            "推荐用法:针对你正在设计的章节主题或某一类评分项,先调一次拿到"
            "相关材料原文,再据此组织目录或正文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "project_info",
                            "company_info",
                            "personnel_info",
                            "scoring_rules",
                            "technical_requirements",
                            "qualification_requirements",
                            "timeline_constraints",
                            "commercial_terms",
                            "compliance_constraints",
                            "risk_signals",
                        ],
                    },
                    "description": (
                        "要检索的实体桶类型(可多选)。每桶含义:project_info=项目背景, "
                        "company_info=公司组织, personnel_info=人员资质, scoring_rules=评分细则, "
                        "technical_requirements=技术要求/SLA, qualification_requirements=投标资质, "
                        "timeline_constraints=工期, commercial_terms=商务条款, "
                        "compliance_constraints=法律合规, risk_signals=风险信号。"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "关键字查询(支持中英文混合);留空则按 entity_types 返桶内前 top_k 条。",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回前 K 条,默认 5,建议不超过 10。",
                    "default": 5,
                },
            },
            "required": ["entity_types"],
        },
    },
}


def make_blackboard_tool_handler(
    entities: dict[str, Any] | None,
    *,
    embeddings: dict[str, list[list[float]]] | None = None,
    query_embedder: Any = None,
    collector: list[dict[str, Any]] | None = None,
) -> Any:
    """生成 ``search_blackboard`` tool 的异步 handler,绑定一份 BlackboardIndex。

    返回 ``async (name, args) -> str``,内部 dispatch tool name 后调
    ``BlackboardIndex.search`` 返 JSON 字符串(LiteLLM tool result 期望 str)。
    未知 tool 名 / 错参数 / index 空都返结构化 error JSON,LLM 据此判断改 query。

    可选参数(D-EK / D-EL):
    - ``embeddings``:与 entities 同形状的桶级向量,启用混合召回
    - ``query_embedder``:``async (text) -> list[float]``,每次 tool 调用前算 query 向量;
      为 None 时退化纯 BM25(即使 entities 带 embeddings)
    - ``collector``:可变 list,handler 把每次命中追加进去(标 ``retrieval_method="tool"``),
      供 write_chapter 节点采集"LLM 看过的资料"落 Chapter.references
    """
    import json as _json

    index = BlackboardIndex(entities, embeddings=embeddings)

    async def handler(name: str, args: dict[str, Any]) -> str:
        if name != "search_blackboard":
            return _json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
        entity_types = args.get("entity_types")
        if isinstance(entity_types, str):
            # 模型偶尔会用字符串而不是数组,容错
            entity_types = [entity_types]
        if not isinstance(entity_types, list):
            return _json.dumps(
                {"error": "entity_types must be a list of bucket names"},
                ensure_ascii=False,
            )
        query = args.get("query") or ""
        if not isinstance(query, str):
            query = str(query)
        top_k_raw = args.get("top_k", 5)
        try:
            top_k = max(1, min(20, int(top_k_raw)))
        except (TypeError, ValueError):
            top_k = 5

        query_embedding: list[float] | None = None
        if query_embedder is not None and query.strip():
            try:
                query_embedding = await query_embedder(query)
            except Exception:
                # embedder 失败回退纯 BM25,不阻塞 tool
                query_embedding = None

        hits = index.search(
            entity_types=[str(t) for t in entity_types],
            query=query,
            top_k=top_k,
            query_embedding=query_embedding,
        )

        if collector is not None:
            for h in hits:
                rec = {
                    "bucket": h.get("bucket"),
                    "content": h.get("content", ""),
                    "retrieval_method": "tool",
                    "score": h.get("score", 0.0),
                }
                if h.get("source_doc"):
                    rec["source_doc"] = h["source_doc"]
                if h.get("section"):
                    rec["section"] = h["section"]
                collector.append(rec)

        # 返最小信息给 LLM,省 token;score 不传(对生成无帮助)
        slim = [
            {
                "bucket": h["bucket"],
                "content": h["content"],
                **({"source_doc": h["source_doc"]} if h.get("source_doc") else {}),
                **({"section": h["section"]} if h.get("section") else {}),
            }
            for h in hits
        ]
        return _json.dumps(
            {"hits": slim, "count": len(slim)}, ensure_ascii=False
        )

    return handler
