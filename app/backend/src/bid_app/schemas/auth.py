"""Auth + me 相关 schemas。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
    """当前用户的三类模型配置。为 NULL 的字段表示使用系统默认值。"""

    llm1_outline_model: str | None  # 提纲生成(LLM-1)
    llm2_chapter_model: str | None  # 正文撰写(LLM-2)
    llm3_visuals_model: str | None  # 配图(LLM-3)

    # 返回系统默认值,前端可展示"当前生效模型"
    default_outline_model: str
    default_chapter_model: str
    default_visuals_model: str

    # 返回已知模型列表供前端下拉选择
    known_models: list[str]


class SetModelConfigRequest(BaseModel):
    """更新模型配置。传 null / 空字符串表示重置为系统默认。"""

    llm1_outline_model: str | None = None
    llm2_chapter_model: str | None = None
    llm3_visuals_model: str | None = None
