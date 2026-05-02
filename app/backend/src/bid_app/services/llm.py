"""LiteLLM 包装,实现 FR-3.9 重试 + FR-3.10 总时长包裹流式收集(§11.1)。

设计要点(D-D):超时必须包住"完整流式收集",不是单个 await。
所以 stream=True 的调用要这样写::

    async with asyncio.timeout(SINGLE_CHAPTER_TIMEOUT_SECONDS):
        async for chunk in stream:
            ...

不能写 ``await asyncio.wait_for(litellm.acompletion(stream=True), 600)``
—— 那只 timeout 第一个 token。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm
import sqlalchemy as sa
import structlog
from litellm.exceptions import (
    APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from ..config import settings
from ..core.error_log import append_error  # ⭐ D-BE
from ..db import session_factory
from ..events.bus import event_bus
from .token_usage import record_token_usage

log = structlog.get_logger()


class LLMRetryFailed(Exception):
    pass


class LLMTimeoutExceeded(Exception):
    pass


class ChapterGenerationFailed(Exception):
    """⭐ D-AU:章节级失败的语义化标记。

    write_chapter 节点(LLM-2)在 LLMRetryFailed / Timeout 后再包一层抛出,
    worker task 用 ``except ChapterGenerationFailed`` 区分:
    - **不**写 errors.log(节点已经把 chapter.last_error 同步到 DB,前端能看到)
    - **不** raise(arq max_tries=1,raise 会让 task 显示 failed,但语义不对)
    - project 状态切 'awaiting_review'(让用户能从 P5 看到 failed 章节并 /retry)
    """

    def __init__(
        self,
        message: str,
        *,
        chapter_index: int,
        chapter_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.chapter_index = chapter_index
        self.chapter_id = chapter_id


_FAKE = os.environ.get("BID_APP_FAKE_LLM") == "1"


@dataclass
class StreamResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


async def call_llm_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    user_id: int | str,
    project_id: int,
    run_id: int | None = None,
    chapter_index: int | None = None,
    **kw: Any,
) -> StreamResult:
    """流式调用 + 重试 + 超时 + 推 SSE token + 记 token_usage。
    返回完整 markdown 与 token 统计。"""
    if _FAKE:
        return await _fake_stream(model, messages, project_id, chapter_index)

    backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
    last_err: Exception | None = None
    timeout_s = settings.single_chapter_timeout_seconds

    # ⭐ D-BG:总超时也要落 errors.log,**包在 try 外**捕 TimeoutError
    try:
        async with asyncio.timeout(timeout_s):
            for attempt in range(settings.llm_retry_max + 1):
                try:
                    return await _do_stream(
                        model,
                        messages,
                        api_key,
                        user_id,
                        project_id,
                        run_id,
                        chapter_index,
                        **kw,
                    )
                except (
                    RateLimitError,
                    ServiceUnavailableError,
                    APIConnectionError,
                    Timeout,
                ) as e:
                    last_err = e
                    log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                    # ⭐ D-BE:每次重试也写 errors.log
                    await _write_llm_error(
                        project_id,
                        f"LLM retry attempt={attempt}",
                        model=model,
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                    if attempt < settings.llm_retry_max:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    await _write_llm_error(
                        project_id,
                        "LLM exhausted",
                        model=model,
                        total_attempts=attempt + 1,
                        last_error=str(e),
                    )
                    raise LLMRetryFailed(str(e)) from e
                except Exception:
                    # 4xx 等不重试
                    raise
    except TimeoutError as te:
        # ⭐ D-BG:外层 asyncio.timeout 触发的总超时(FR-3.10 = 600s 兜底)
        await _write_llm_error(
            project_id,
            "LLM total timeout",
            model=model,
            timeout_seconds=timeout_s,
            last_error=str(last_err) if last_err else None,
        )
        raise LLMTimeoutExceeded(
            f"LLM stream exceeded {timeout_s}s total timeout"
        ) from te

    raise LLMRetryFailed(str(last_err))


async def _llm_project_dir(project_id: int) -> Path:
    """D-BE:取项目目录给 errors.log 用。"""
    async with session_factory() as s:
        row = await s.execute(
            sa.text("SELECT dir_path FROM projects WHERE id=:p"),
            {"p": project_id},
        )
        return Path(row.scalar_one())


async def _write_llm_error(project_id: int, message: str, **fields: Any) -> None:
    """⭐ D-BE:LLM 错误日志写入项目级 errors.log,失败永不传播。"""
    try:
        pdir = await _llm_project_dir(project_id)
        await append_error(pdir, message, **fields)
    except Exception:
        log.exception(
            "llm_error_log_write_failed", project_id=project_id, message=message
        )


async def _do_stream(
    model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    user_id: int | str,
    project_id: int,
    run_id: int | None,
    chapter_index: int | None,
    **kw: Any,
) -> StreamResult:
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        api_key=api_key,
        stream=True,
        stream_options={"include_usage": True},
        **kw,
    )

    chunks: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    async for chunk in response:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            chunks.append(delta)
            if chapter_index is not None:
                await event_bus.publish(
                    project_id,
                    {
                        "type": "chapter_token",
                        "chapter_index": chapter_index,
                        "delta": delta,
                    },
                )
        usage = getattr(chunk, "usage", None)
        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

    text = "".join(chunks)
    await record_token_usage(
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return StreamResult(
        text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )


async def call_llm_json(
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    user_id: int | str,
    project_id: int,
    run_id: int | None = None,
    timeout_seconds: int | None = None,
    **kw: Any,
) -> tuple[dict[str, Any], StreamResult]:
    """非流式(LLM-1 / LLM-3 用)+ 重试 + 超时 + JSON 解析。

    与 call_llm_stream 不同:
    - stream=False,一次性拿 response
    - response_format=json_object 强制 JSON
    - 超时默认 120s

    返回 ``(parsed_json, stream_result)``。``stream_result.text`` 是原始 JSON 字符串。
    """
    if _FAKE:
        return await _fake_json(model, messages, project_id)

    backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
    timeout = timeout_seconds or 120
    last_err: Exception | None = None

    try:
        async with asyncio.timeout(timeout):
            for attempt in range(settings.llm_retry_max + 1):
                try:
                    response = await litellm.acompletion(
                        model=model,
                        messages=messages,
                        api_key=api_key,
                        stream=False,
                        response_format={"type": "json_object"},
                        **kw,
                    )
                    content = response.choices[0].message.content or "{}"
                    usage = getattr(response, "usage", None)
                    p_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
                    c_tok = getattr(usage, "completion_tokens", 0) if usage else 0

                    await record_token_usage(
                        user_id=user_id,
                        project_id=project_id,
                        run_id=run_id,
                        model=model,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                    )

                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError as je:
                        log.warning(
                            "llm_json_parse_failed",
                            model=model,
                            content_head=content[:200],
                        )
                        # ⭐ D-BO:JSON 解析失败也写 errors.log
                        last_err = je
                        await _write_llm_error(
                            project_id,
                            f"LLM retry attempt={attempt}",
                            model=model,
                            mode="json",
                            error_type="JSONDecodeError",
                            error=str(je),
                            content_head=content[:200],
                        )
                        raise LLMRetryFailed(f"json parse: {je}") from je

                    return parsed, StreamResult(
                        text=content,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                    )

                except (
                    RateLimitError,
                    ServiceUnavailableError,
                    APIConnectionError,
                    Timeout,
                ) as e:
                    last_err = e
                    log.warning("llm_retry", model=model, attempt=attempt, error=str(e))
                    await _write_llm_error(
                        project_id,
                        f"LLM retry attempt={attempt}",
                        model=model,
                        mode="json",
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                    if attempt < settings.llm_retry_max:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    await _write_llm_error(
                        project_id,
                        "LLM exhausted",
                        model=model,
                        mode="json",
                        total_attempts=attempt + 1,
                        last_error=str(e),
                    )
                    raise LLMRetryFailed(str(e)) from e
                except LLMRetryFailed:
                    # JSON 解析失败也走重试链
                    if attempt < settings.llm_retry_max:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    await _write_llm_error(
                        project_id,
                        "LLM exhausted",
                        model=model,
                        mode="json",
                        total_attempts=attempt + 1,
                        last_error=str(last_err) if last_err else "json parse",
                    )
                    raise
    except TimeoutError as te:
        await _write_llm_error(
            project_id,
            "LLM total timeout",
            model=model,
            mode="json",
            timeout_seconds=timeout,
            last_error=str(last_err) if last_err else None,
        )
        raise LLMTimeoutExceeded(
            f"LLM JSON exceeded {timeout}s total timeout"
        ) from te

    raise LLMRetryFailed(str(last_err))


async def _fake_stream(
    model: str,
    messages: list[dict[str, Any]],
    project_id: int,
    chapter_index: int | None,
) -> StreamResult:
    """``BID_APP_FAKE_LLM=1`` 时用,不调外网(§18.2)。"""
    fake = "# 章节标题\n\n这是测试用的章节正文。" + "占位段落。" * 50
    if chapter_index is not None:
        for ch in fake:
            await event_bus.publish(
                project_id,
                {
                    "type": "chapter_token",
                    "chapter_index": chapter_index,
                    "delta": ch,
                },
            )
    return StreamResult(text=fake, prompt_tokens=100, completion_tokens=200)


async def _fake_json(
    model: str, messages: list[dict[str, Any]], project_id: int
) -> tuple[dict[str, Any], StreamResult]:
    """``BID_APP_FAKE_LLM=1`` 时用,不调外网。"""
    fake: dict[str, Any] = {
        "chapters": [
            {
                "id": "ch_01",
                "title": "测试章节 1",
                "summary": "测试摘要",
                "key_points": ["点1", "点2"],
                "target_pages": 2,
                "matched_scoring_items": ["1.1"],
            },
            {
                "id": "ch_02",
                "title": "测试章节 2",
                "summary": "测试摘要 2",
                "key_points": ["点A", "点B"],
                "target_pages": 3,
                "matched_scoring_items": ["2.1"],
            },
        ]
    }
    return fake, StreamResult(
        text=json.dumps(fake, ensure_ascii=False),
        prompt_tokens=50,
        completion_tokens=80,
    )
