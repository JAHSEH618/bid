"""D-EO: embedding model 可配置 — resolve_models 包含 embedding_model。"""
from __future__ import annotations

from bid_app.workflow.resolve import ResolvedModels


def test_resolved_models_has_embedding_field() -> None:
    """ResolvedModels 必须四个字段齐全(outline / chapter / visuals / embedding)。"""
    rm = ResolvedModels(
        outline_model="dashscope/qwen-max",
        chapter_model="dashscope/qwen-max",
        visuals_model="dashscope/qwen-flash",
        embedding_model="dashscope/text-embedding-v3",
    )
    assert rm.embedding_model == "dashscope/text-embedding-v3"


def test_start_request_accepts_embedding_model() -> None:
    """StartRequest 接受 embedding_model 字段。"""
    from bid_app.schemas.projects import StartRequest

    body = StartRequest(
        pages_per_chapter=3,
        max_retry_per_chapter=3,
        outline_model="dashscope/qwen-max",
        chapter_model="dashscope/qwen-max",
        visuals_model="dashscope/qwen-flash",
        embedding_model="dashscope/text-embedding-v3",
    )
    assert body.embedding_model == "dashscope/text-embedding-v3"

    # 字段可缺省(None)— /start 路径会回退 user / settings
    body2 = StartRequest(pages_per_chapter=3, max_retry_per_chapter=3)
    assert body2.embedding_model is None


def test_model_config_response_has_embedding_default() -> None:
    """ModelConfigResponse 暴露 default_embedding_model 给前端展示。"""
    from bid_app.schemas.auth import KNOWN_MODELS, ModelConfigResponse

    # text-embedding-v3 必须在 KNOWN_MODELS 里,前端下拉才能选
    assert "dashscope/text-embedding-v3" in KNOWN_MODELS

    resp = ModelConfigResponse(
        llm1_outline_model=None,
        llm2_chapter_model=None,
        llm3_visuals_model=None,
        llm4_embedding_model=None,
        default_outline_model="dashscope/qwen-max",
        default_chapter_model="dashscope/qwen-max",
        default_visuals_model="dashscope/qwen-flash",
        default_embedding_model="dashscope/text-embedding-v3",
        known_models=KNOWN_MODELS,
        custom_models=[],
        available_models=[],
    )
    assert resp.default_embedding_model == "dashscope/text-embedding-v3"


def test_set_model_config_request_accepts_llm4() -> None:
    from bid_app.schemas.auth import SetModelConfigRequest

    body = SetModelConfigRequest(
        custom_models=["dashscope/text-embedding-v3"],
        llm4_embedding_model="dashscope/text-embedding-v3",
    )
    assert body.llm4_embedding_model == "dashscope/text-embedding-v3"
