"""D-EK: embed_texts 失败回退测试(原生 DashScope API,不走 LiteLLM)。"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from bid_app.services import embeddings as emb


def _make_text_embedding_response(texts: list[str], dim: int = 1024) -> dict[str, Any]:
    """模拟 DashScope text-embedding 接口的成功响应。"""
    return {
        "output": {
            "embeddings": [
                {"text_index": i, "embedding": [float(i)] * dim}
                for i in range(len(texts))
            ]
        }
    }


@pytest.mark.asyncio
async def test_embed_texts_empty_input() -> None:
    out = await emb.embed_texts([], api_key="sk-fake")
    assert out == []


@pytest.mark.asyncio
async def test_embed_texts_all_empty_strings_returns_zero_vecs() -> None:
    out = await emb.embed_texts(["", "   ", ""], api_key="sk-fake")
    assert len(out) == 3
    for v in out:
        assert len(v) == emb.EMBEDDING_DIM
        assert all(x == 0.0 for x in v)


@pytest.mark.asyncio
async def test_embed_texts_dashscope_failure_falls_back_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx 抛异常 → 该批全零,不抛。"""

    async def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("DashScope outage")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    out = await emb.embed_texts(["hello", "world"], api_key="sk-fake")
    assert len(out) == 2
    for v in out:
        assert v == [0.0] * emb.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_embed_texts_404_unsupported_model_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DashScope 返 404 unsupported model → 全零回退,工作流不阻塞。"""

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "Unsupported model",
                    "code": "model_not_supported",
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    out = await emb.embed_texts(["hi"], api_key="sk-fake")
    assert len(out) == 1
    assert out[0] == [0.0] * emb.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_embed_texts_batches_above_25(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过 25 条 → 多批调用,顺序保持(走 text-embedding 路径)。"""
    calls: list[int] = []

    async def _fake_post(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        body = kwargs.get("json") or {}
        texts = ((body.get("input") or {}).get("texts") or [])
        calls.append(len(texts))
        return httpx.Response(
            200,
            json=_make_text_embedding_response(texts),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    texts = [f"text {i}" for i in range(60)]
    out = await emb.embed_texts(texts, api_key="sk-fake")
    assert len(out) == 60
    assert calls == [25, 25, 10]


@pytest.mark.asyncio
async def test_embed_texts_multimodal_route_for_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vision / multimodal 模型走 multimodal-embedding 端点 + ``input.contents``。"""
    used_urls: list[str] = []
    payload_keys: list[str] = []

    async def _fake_post(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        used_urls.append(url)
        body = kwargs.get("json") or {}
        payload_keys.append(",".join((body.get("input") or {}).keys()))
        # 模拟多模态接口响应
        contents = (body.get("input") or {}).get("contents") or []
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"index": i, "embedding": [0.1] * 1024}
                        for i in range(len(contents))
                    ]
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    out = await emb.embed_texts(
        ["a", "b"],
        api_key="sk-fake",
        model="dashscope/tongyi-embedding-vision-plus-2026-03-06",
    )
    assert len(out) == 2
    assert all(len(v) == 1024 for v in out)
    assert "multimodal-embedding" in used_urls[0]
    assert "contents" in payload_keys[0]


@pytest.mark.asyncio
async def test_cosine_similarity_basic() -> None:
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert emb.cosine_similarity(v1, v2) == pytest.approx(1.0)

    v3 = [0.0, 1.0, 0.0]
    assert emb.cosine_similarity(v1, v3) == pytest.approx(0.0)

    # 全零向量 → 返 0,不要 NaN
    assert emb.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert emb.cosine_similarity([], [1.0]) == 0.0


@pytest.mark.asyncio
async def test_embed_one_empty_returns_zero() -> None:
    out = await emb.embed_one("", api_key="sk-fake")
    assert out == [0.0] * emb.EMBEDDING_DIM

