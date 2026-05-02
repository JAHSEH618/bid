"""DashScope API Key 连通性验证(§15.5,M2-5)。

走 LiteLLM ``acompletion`` 发一条最小请求,1 token 验证 key 有效。
失败抛 ``ApiKeyValidationFailed``,API 端点 catch 后转 400 / {ok: false}。

⚠️ 不在这层做 token 计费(避免把 validator 测试 token 算进用户配额);
litellm 的 ``record_token_usage`` 只在业务路径 ``call_llm_stream`` /
``call_llm_json`` 内部调,本 validator 不走那两个入口。
"""
from __future__ import annotations

import asyncio

import litellm
import structlog

from ..config import settings

log = structlog.get_logger()

# 默认走 LLM-3 模型(qwen-flash,便宜,响应快)做连通测试
_TEST_MODEL = settings.llm3_visuals_model
_TEST_TIMEOUT_SECONDS = 30


class ApiKeyValidationFailed(Exception):
    pass


async def validate_dashscope(api_key: str) -> None:
    """对 DashScope 发一条 1-token 请求验连通。

    成功:return None。
    失败:raise ``ApiKeyValidationFailed`` 带原因。
    """
    if not api_key or len(api_key) < 8:
        raise ApiKeyValidationFailed("api_key 太短或为空")

    try:
        async with asyncio.timeout(_TEST_TIMEOUT_SECONDS):
            response = await litellm.acompletion(
                model=_TEST_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                api_key=api_key,
                max_tokens=1,
                stream=False,
            )
    except asyncio.TimeoutError as e:
        raise ApiKeyValidationFailed(
            f"DashScope 验证超时(>{_TEST_TIMEOUT_SECONDS}s)"
        ) from e
    except Exception as e:
        # litellm 可能抛 AuthenticationError / RateLimitError /
        # APIConnectionError 等,统一转
        log.warning(
            "api_key_validation_litellm_error",
            error_type=type(e).__name__,
            error=str(e),
        )
        raise ApiKeyValidationFailed(f"{type(e).__name__}: {e}") from e

    # 进一步校验响应基本结构
    choices = getattr(response, "choices", None)
    if not choices:
        raise ApiKeyValidationFailed(
            "DashScope 返回空 choices(模型可能不可用)"
        )
