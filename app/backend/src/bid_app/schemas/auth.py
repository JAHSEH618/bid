"""Auth + me 相关 schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


class MeResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class SetApiKeyRequest(BaseModel):
    key: str = Field(..., min_length=8, max_length=256)


class ApiKeyInfoResponse(BaseModel):
    """API Key 元信息(明文 key 永不返回)。"""

    provider: str
    masked: str  # sk-***xxxx,只露最后 4 位
    last_validated_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


class TokenUsageRow(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int


class TokenUsageSummary(BaseModel):
    user_id: int
    period: str  # e.g. "month" / "all"
    rows: list[TokenUsageRow]
    total_prompt: int
    total_completion: int


# === 模型配置(§0002) ===

# 已知的百炼模型列表(前端下拉用),LiteLLM provider/model 格式
KNOWN_MODELS: list[str] = [
    "dashscope/deepseek-v4-flash",
    "dashscope/deepseek-v3",
    "dashscope/deepseek-r1",
    "dashscope/qwen3.6-max-preview",
    "dashscope/qwen3.6-flash",
    "dashscope/qwen-max",
    "dashscope/qwen-plus",
    "dashscope/qwen-turbo",
]


class ModelConfigResponse(BaseModel):
    """当前用户的模型池。

    llm1/2/3 字段保留给旧前端兼容;新前端只用 custom_models /
    available_models,在项目启动与章节确认时再选择具体用途。
    """

    llm1_outline_model: str | None  # 提纲生成(LLM-1)
    llm2_chapter_model: str | None  # 正文撰写(LLM-2)
    llm3_visuals_model: str | None  # 配图(LLM-3)

    # 返回系统默认值,前端可展示"当前生效模型"
    default_outline_model: str
    default_chapter_model: str
    default_visuals_model: str

    # 返回已知模型列表供前端下拉选择
    known_models: list[str]
    custom_models: list[str]
    available_models: list[str]


class SetModelConfigRequest(BaseModel):
    """更新模型池。

    llm1/2/3 字段保留兼容旧调用;新调用只传 custom_models。
    """

    llm1_outline_model: str | None = None
    llm2_chapter_model: str | None = None
    llm3_visuals_model: str | None = None
    custom_models: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("custom_models")
    @classmethod
    def _normalize_custom_models(cls, v: list[str]) -> list[str]:
        # FR-3.3 / FR-7:本期 LLM 一律走 DashScope(项目快照的是 DashScope
        # API Key)。允许任意 LiteLLM provider 字符串会让用户把模型设为
        # 比如 ``openai/gpt-4o``,litellm.acompletion 拿着 DashScope key
        # 去打 OpenAI 失败,或更糟 — 配上对应 provider 的 key 后绕过 LLM
        # 边界声明。统一在此拦截。
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            model = item.strip()
            if not model or model in seen:
                continue
            if len(model) > 128:
                raise ValueError("model name must be <= 128 chars")
            if not model.startswith("dashscope/"):
                raise ValueError(
                    f"only DashScope models are allowed (got '{model}'); "
                    "model must start with 'dashscope/'"
                )
            out.append(model)
            seen.add(model)
        return out
