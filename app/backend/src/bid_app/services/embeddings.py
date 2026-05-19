"""DashScope text-embedding-v3 调用包装(D-EK,2026-05-19)。

为 Phase A 混合召回服务:把黑板条目和章节查询文本转成 1024 维向量,与
BM25 排名做 RRF 融合。

设计要点:
- 批量上限 25(DashScope text-embedding-v3 单次最多 25 条),自动分批
- 失败回退全零向量 + warning,不抛异常 — 工作流降级到纯 BM25,不阻塞
- 用 litellm.aembedding 复用 settings.llm 路由 / 限流逻辑
- 返回 list[list[float]],与输入 texts 顺序对齐;失败位用全零占位

不依赖 numpy(项目当前无 numpy 依赖,纯 Python list 在百级条目规模下
完全够用)。
"""

from __future__ import annotations

import math
from typing import Any

import litellm
import structlog

from ..config import settings

log = structlog.get_logger()


EMBEDDING_DIM = 1024
"""text-embedding-v3 标准输出维度。失败回退全零向量时也用这个尺寸。

注意:DashScope 的多模态 embedding(``tongyi-embedding-vision-*``)实际可能是
其他维度。回退全零向量时仍按 1024 维生成,后续 BlackboardIndex 检查长度匹配
不上会自动丢弃向量退化纯 BM25,所以维度对不上时不会污染检索结果。
"""

_BATCH_SIZE = 25
"""DashScope 单次 embedding 输入上限。"""

_DASHSCOPE_OAI_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
"""DashScope 的 OpenAI 兼容端点,所有 embedding 模型都走这里。LiteLLM 的
``dashscope/`` provider 没覆盖所有新模型(2026 年的 tongyi-embedding-vision-*
没在 model_prices 表里),会报 ``Unmapped LLM provider``。改走 ``openai/``
路由 + 显式 api_base 绕过该限制,DashScope 兼容 OpenAI embedding 协议。
"""


def _route_for_embedding(model: str) -> tuple[str, str | None]:
    """决定调 LiteLLM 时用什么 model id 与 api_base。

    - ``dashscope/xxx`` → ``openai/xxx`` + DashScope 兼容端点
      (LiteLLM dashscope embedding provider 不识别新模型时的兜底)
    - 其他前缀 → 原样传,api_base=None
    """
    if model.startswith("dashscope/"):
        suffix = model[len("dashscope/") :]
        return f"openai/{suffix}", _DASHSCOPE_OAI_BASE
    return model, None


def _zero_vec() -> list[float]:
    return [0.0] * EMBEDDING_DIM


async def embed_texts(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    user_id: int | str | None = None,
    project_id: int | None = None,
) -> list[list[float]]:
    """批量把文本转向量。

    顺序与输入对齐,失败位置用全零向量占位。空 / None 文本也输出全零。
    DashScope 一次最多 25 条,内部自动分批串行调用(并发开销 < 模型 RT)。

    失败语义:任意一批失败,该批所有位置全零 + log warning,**不抛**。
    """
    if not texts:
        return []
    model = model or settings.embedding_model
    route_model, api_base = _route_for_embedding(model)
    out: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        # 过滤空文本:DashScope 对空串报 400;空位用全零占位,后面合并时按索引对齐
        non_empty_idx = [i for i, t in enumerate(batch) if isinstance(t, str) and t.strip()]
        non_empty_texts = [batch[i] for i in non_empty_idx]
        batch_out: list[list[float]] = [_zero_vec()] * len(batch)
        if not non_empty_texts:
            out.extend(batch_out)
            continue
        try:
            kwargs: dict[str, Any] = {
                "model": route_model,
                "input": non_empty_texts,
                "api_key": api_key,
            }
            if api_base is not None:
                kwargs["api_base"] = api_base
            if user_id is not None:
                kwargs["user"] = str(user_id)
            resp = await litellm.aembedding(**kwargs)
            data = resp["data"] if isinstance(resp, dict) else resp.data
            # data 按 input 顺序回,每项 {embedding: list[float], index: int}
            actual_dim: int | None = None
            for local_i, item in enumerate(data):
                emb = item["embedding"] if isinstance(item, dict) else item.embedding
                if not isinstance(emb, list):
                    continue
                if actual_dim is None:
                    actual_dim = len(emb)
                # 接受任意维度,但同批次内维度必须一致(否则下游 cosine 算不出)
                if len(emb) != actual_dim:
                    continue
                batch_out[non_empty_idx[local_i]] = [float(x) for x in emb]
        except Exception as e:
            log.warning(
                "embedding_batch_failed_fallback_zero",
                project_id=project_id,
                model=model,
                route_model=route_model,
                batch_size=len(non_empty_texts),
                error=repr(e),
            )
        out.extend(batch_out)
    return out


async def embed_one(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    user_id: int | str | None = None,
    project_id: int | None = None,
) -> list[float]:
    """单文本 embedding 便捷调用。空 / 失败返全零。"""
    if not text or not text.strip():
        return _zero_vec()
    vecs = await embed_texts(
        [text],
        api_key=api_key,
        model=model,
        user_id=user_id,
        project_id=project_id,
    )
    return vecs[0] if vecs else _zero_vec()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python cosine。1024 维 ~50μs/次,百级条目无压力。

    任一向量为全零(失败回退 / 空文本)直接返 0。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


__all__ = ["EMBEDDING_DIM", "cosine_similarity", "embed_one", "embed_texts"]
