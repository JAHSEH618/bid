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
) -> Any:
    """生成 ``search_blackboard`` tool 的异步 handler,绑定一份 BlackboardIndex。

    返回 ``async (name, args) -> str``,内部 dispatch tool name 后调
    ``BlackboardIndex.search`` 返 JSON 字符串(LiteLLM tool result 期望 str)。
    未知 tool 名 / 错参数 / index 空都返结构化 error JSON,LLM 据此判断改 query。
    """
    import json as _json

    index = BlackboardIndex(entities)

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
        hits = index.search(
            entity_types=[str(t) for t in entity_types],
            query=query,
            top_k=top_k,
        )
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
