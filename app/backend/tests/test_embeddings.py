"""D-EK: embed_texts 失败回退测试。"""
from __future__ import annotations

import pytest

from bid_app.services import embeddings as emb


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
async def test_embed_texts_dashscope_failure_falls_back_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """litellm 抛异常 → 该批全零,不抛。"""

    async def _boom(**kw: object) -> object:
        raise RuntimeError("DashScope outage")

    monkeypatch.setattr("litellm.aembedding", _boom)
    out = await emb.embed_texts(["hello", "world"], api_key="sk-fake")
    assert len(out) == 2
    for v in out:
        assert v == [0.0] * emb.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_embed_texts_batches_above_25(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 25 条 → 多批调用,顺序保持。"""
    calls: list[int] = []

    async def _stub(**kw: object) -> dict[str, object]:
        batch = kw.get("input")
        assert isinstance(batch, list)
        assert len(batch) <= 25
        calls.append(len(batch))
        return {
            "data": [
                {"embedding": [float(i)] * emb.EMBEDDING_DIM, "index": i}
                for i in range(len(batch))
            ]
        }

    monkeypatch.setattr("litellm.aembedding", _stub)
    texts = [f"text {i}" for i in range(60)]
    out = await emb.embed_texts(texts, api_key="sk-fake")
    assert len(out) == 60
    assert calls == [25, 25, 10]


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
