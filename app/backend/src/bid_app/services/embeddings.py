"""DashScope embedding 原生 API 调用(D-EK / D-EQ Phase 2,2026-05-19)。

⚠️ **不走 LiteLLM**。原因:
- LiteLLM 的 ``dashscope/`` provider 没覆盖 2026 新模型(``tongyi-embedding-vision-*``),
  报 ``Unmapped LLM provider``
- 用 ``openai/`` + 兼容模式 ``api_base`` 路由,DashScope 的兼容端点也只支持
  ``text-embedding-v1/v2/v3``,新多模态模型直接 404 ``Unsupported model``

直接对接 DashScope 原生 REST API,按模型名路由:
- ``text-embedding-*``:``/api/v1/services/embeddings/text-embedding/text-embedding``
  请求体 ``input.texts``,批量上限 25,响应 ``output.embeddings[].embedding``
- ``tongyi-embedding-vision-*`` 等多模态:
  ``/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding``
  请求体 ``input.contents[].text``,批量上限 ~10,响应 ``output.embeddings[].embedding``

失败回退全零向量 + warning,不抛异常 — 工作流降级到纯 BM25。
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import structlog

from ..config import settings

log = structlog.get_logger()


EMBEDDING_DIM = 1024
"""text-embedding-v3 标准输出维度。BlackboardIndex 实际按返回维度自适应,
此常量仅用于失败回退时的占位向量。"""

_DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/api/v1/services/embeddings"
_TEXT_BATCH_SIZE = 25
"""text-embedding-* 单次最多 25 条。"""
_VISION_BATCH_SIZE = 10
"""multimodal-embedding 单次最多 10 条(多模态接口更严)。"""
_HTTP_TIMEOUT = 30.0


def _zero_vec() -> list[float]:
    return [0.0] * EMBEDDING_DIM


def _is_multimodal(model_short: str) -> bool:
    """是不是多模态 embedding 模型(走 multimodal-embedding 端点)。

    DashScope 把 ``tongyi-embedding-vision-*`` / ``multimodal-embedding-*``
    归为多模态接口;``text-embedding-v*`` 走文本接口。
    """
    name = model_short.lower()
    return "multimodal" in name or "vision" in name


def _strip_provider(model: str) -> str:
    """剥掉 ``dashscope/`` 前缀,DashScope 原生接口只认裸模型名。"""
    if model.startswith("dashscope/"):
        return model[len("dashscope/") :]
    return model


async def _call_text_embedding(
    client: httpx.AsyncClient,
    *,
    model_short: str,
    texts: list[str],
    api_key: str,
) -> list[list[float]]:
    """文本 embedding 接口。空文本由 caller 过滤,这里不再判空。"""
    url = f"{_DASHSCOPE_API_BASE}/text-embedding/text-embedding"
    payload: dict[str, Any] = {
        "model": model_short,
        "input": {"texts": texts},
    }
    resp = await client.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = ((data or {}).get("output") or {}).get("embeddings") or []
    # 按 text_index 排序,确保与 input.texts 对齐
    out: list[list[float]] = [[] for _ in texts]
    for item in embeddings:
        if not isinstance(item, dict):
            continue
        idx = item.get("text_index", -1)
        emb = item.get("embedding")
        if isinstance(idx, int) and 0 <= idx < len(out) and isinstance(emb, list):
            out[idx] = [float(x) for x in emb]
    return out


async def _call_multimodal_embedding(
    client: httpx.AsyncClient,
    *,
    model_short: str,
    texts: list[str],
    api_key: str,
) -> list[list[float]]:
    """多模态 embedding 接口。这里只走 text 子项;图像 / 视频暂不支持。"""
    url = f"{_DASHSCOPE_API_BASE}/multimodal-embedding/multimodal-embedding"
    contents = [{"text": t} for t in texts]
    payload: dict[str, Any] = {
        "model": model_short,
        "input": {"contents": contents},
    }
    resp = await client.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = ((data or {}).get("output") or {}).get("embeddings") or []
    out: list[list[float]] = [[] for _ in texts]
    for item in embeddings:
        if not isinstance(item, dict):
            continue
        # 兼容 index / text_index 两种字段名(不同模型返回略不同)
        idx_raw = item.get("index", item.get("text_index", -1))
        emb = item.get("embedding")
        if isinstance(idx_raw, int) and 0 <= idx_raw < len(out) and isinstance(emb, list):
            out[idx_raw] = [float(x) for x in emb]
    return out


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
    按模型名路由到 ``text-embedding`` 或 ``multimodal-embedding`` 接口。

    失败语义:任意一批失败,该批所有位置全零 + log warning,**不抛**。
    """
    if not texts:
        return []
    _ = user_id  # token_usage 暂不记账 embedding,保留参数兼容
    model = model or settings.embedding_model
    model_short = _strip_provider(model)
    multimodal = _is_multimodal(model_short)
    batch_size = _VISION_BATCH_SIZE if multimodal else _TEXT_BATCH_SIZE
    out: list[list[float]] = []
    async with httpx.AsyncClient() as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            # 过滤空文本,占位全零保持顺序
            non_empty_idx = [
                i for i, t in enumerate(batch) if isinstance(t, str) and t.strip()
            ]
            non_empty_texts = [batch[i].strip() for i in non_empty_idx]
            batch_out: list[list[float]] = [_zero_vec()] * len(batch)
            if not non_empty_texts:
                out.extend(batch_out)
                continue
            try:
                if multimodal:
                    vecs = await _call_multimodal_embedding(
                        client,
                        model_short=model_short,
                        texts=non_empty_texts,
                        api_key=api_key,
                    )
                else:
                    vecs = await _call_text_embedding(
                        client,
                        model_short=model_short,
                        texts=non_empty_texts,
                        api_key=api_key,
                    )
                for local_i, vec in enumerate(vecs):
                    if vec:
                        batch_out[non_empty_idx[local_i]] = vec
            except httpx.HTTPStatusError as e:
                log.warning(
                    "embedding_batch_failed_fallback_zero",
                    project_id=project_id,
                    model=model,
                    batch_size=len(non_empty_texts),
                    status_code=e.response.status_code,
                    error=e.response.text[:500],
                )
            except Exception as e:
                log.warning(
                    "embedding_batch_failed_fallback_zero",
                    project_id=project_id,
                    model=model,
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
