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
import re
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
from .redaction import RedactionContext, redact_messages
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
    on_partial: Any = None,  # async callable[[str], Awaitable[None]] | None
    redaction_ctx: RedactionContext | None = None,
    **kw: Any,
) -> StreamResult:
    """流式调用 + 重试 + 超时 + 推 SSE token + 记 token_usage。
    返回完整 markdown 与 token 统计。

    ⭐ R-14:``on_partial(text)`` 是可选 async 回调,流式生成期间每累积
    100 chunks **或** 距上次回调 ≥ 1s 触发一次,传入"截至此刻完整 partial
    markdown"。write_chapter 用它 periodic flush DB(让用户刷新页面也
    能看到流走的内容)。回调内部异常被 swallow,不打断流。

    ⭐ D3 (PR-M6-1):所有 messages.content 在出栈点统一脱敏。caller 不传
    ``redaction_ctx`` 时函数内自建一个;同一调用内重复出现的敏感值得到
    同一占位符。脱敏映射不持久化,函数返回后 GC。
    """
    if _FAKE:
        return await _fake_stream(
            model, messages, project_id, chapter_index, on_partial
        )

    # ⭐ D3:出栈前脱敏。同一 retry 链共用同一 ctx → 占位符一致。
    if redaction_ctx is None:
        redaction_ctx = RedactionContext()
    redacted_messages = redact_messages(messages, redaction_ctx)

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
                        redacted_messages,
                        api_key,
                        user_id,
                        project_id,
                        run_id,
                        chapter_index,
                        on_partial,
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


_PARTIAL_FLUSH_EVERY_CHUNKS = 100  # ⭐ R-14:每 100 chunk 触发 on_partial
_PARTIAL_FLUSH_EVERY_SECONDS = 1.0  # 或距上次 ≥ 1s


async def _do_stream(
    model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    user_id: int | str,
    project_id: int,
    run_id: int | None,
    chapter_index: int | None,
    on_partial: Any = None,  # async callable[[str], Awaitable[None]] | None
    **kw: Any,
) -> StreamResult:
    import time as _time

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

    # ⭐ R-14:periodic flush 状态(only 流式有 chapter_index 时启用)
    chunks_since_flush = 0
    last_flush_at = _time.monotonic()

    async def _maybe_flush() -> None:
        """if on_partial 设置且达到阈值,await 之 + 重置计数器。
        on_partial 抛任何异常都 swallow + log,**不能打断 LLM 流**。"""
        nonlocal chunks_since_flush, last_flush_at
        if on_partial is None:
            return
        try:
            await on_partial("".join(chunks))
        except Exception:
            log.exception(
                "llm_on_partial_callback_failed",
                project_id=project_id,
                chapter_index=chapter_index,
            )
        chunks_since_flush = 0
        last_flush_at = _time.monotonic()

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
            # ⭐ R-14:flush by chunk count or wall time
            chunks_since_flush += 1
            if (
                chunks_since_flush >= _PARTIAL_FLUSH_EVERY_CHUNKS
                or (_time.monotonic() - last_flush_at)
                >= _PARTIAL_FLUSH_EVERY_SECONDS
            ):
                await _maybe_flush()
        usage = getattr(chunk, "usage", None)
        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

    text = "".join(chunks)
    # ⭐ R-14 final flush:确保最后一段 token 也落 DB(完整文本由 caller 写
    # final_text + status='awaiting_review',这里只是兜底防最后 < 100 chunks
    # 没触发阈值时 partial 缺尾段)
    if on_partial is not None and chunks_since_flush > 0:
        try:
            await on_partial(text)
        except Exception:
            log.exception(
                "llm_on_partial_final_flush_failed",
                project_id=project_id,
                chapter_index=chapter_index,
            )
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
    redaction_ctx: RedactionContext | None = None,
    **kw: Any,
) -> tuple[dict[str, Any], StreamResult]:
    """非流式(LLM-1 / LLM-3 用)+ 重试 + 超时 + JSON 解析。

    与 call_llm_stream 不同:
    - stream=False,一次性拿 response
    - response_format=json_object 强制 JSON
    - 超时默认 120s

    返回 ``(parsed_json, stream_result)``。``stream_result.text`` 是原始 JSON 字符串。

    ⭐ D3 (PR-M6-1):同 ``call_llm_stream``,出栈前对 messages 脱敏。
    """
    if _FAKE:
        return await _fake_json(model, messages, project_id)

    if redaction_ctx is None:
        redaction_ctx = RedactionContext()
    redacted_messages = redact_messages(messages, redaction_ctx)

    backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
    timeout = timeout_seconds or 120
    last_err: Exception | None = None

    try:
        async with asyncio.timeout(timeout):
            for attempt in range(settings.llm_retry_max + 1):
                try:
                    response = await litellm.acompletion(
                        model=model,
                        messages=redacted_messages,
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


# Phase 2B (2026-05-16):tool calling 版本的 JSON 调用。LLM 可以在最终输
# 出 JSON 前发起多轮 search_blackboard 工具调用,模型自主决定调什么 / 调
# 几次。与 ``call_llm_json`` 同一份重试 / 超时 / token 计费骨架,差别只在
# 内层加 tool loop。
#
# 限制:
# - response_format=json_object 跟 tools 在 DashScope 上不能同时给(模型
#   收到 tools 时不响应 response_format),所以本路径**不**强制 JSON,
#   完全依赖 system prompt 引导。最终 content 走 ``json.loads`` + 容错抽取。
# - 单轮 LLM call 不重试,只整个 round 失败时整体重试。max_tool_rounds
#   超出时强迫终止:把 conversation 加一条「don't call any more tools,
#   give the final answer」再问一次,仍 tool_call 就抛 LLMRetryFailed。

ToolHandler = Any  # async callable: (name: str, args: dict) -> str  (JSON-encoded result)


async def call_llm_with_tools_json(
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    user_id: int | str,
    project_id: int,
    tools: list[dict[str, Any]],
    tool_handler: ToolHandler,
    max_tool_rounds: int = 6,
    run_id: int | None = None,
    timeout_seconds: int | None = None,
    redaction_ctx: RedactionContext | None = None,
    **kw: Any,
) -> tuple[dict[str, Any], StreamResult]:
    """Tool calling 形式的 JSON 调用。

    流程:
    1. system / user prompt 进 conversation
    2. 一轮 acompletion(带 tools);LLM 返 tool_calls → 执行 → tool result 入 conversation → 下一轮
    3. LLM 返纯 content(无 tool_calls)→ ``json.loads`` 解析,返结果
    4. round 超过 max_tool_rounds 仍 tool_call → 抛 ``LLMRetryFailed``

    所有 round 的 token 累加后一次性 ``record_token_usage``。
    """
    if _FAKE:
        return await _fake_json(model, messages, project_id)

    if redaction_ctx is None:
        redaction_ctx = RedactionContext()
    # 注意:tool result 也可能含原文(虽然 entities 已经是脱敏的),为安全
    # 起见,每次拼 conversation 时只对原始 messages 脱敏(已经做过),
    # tool result 由 handler 自己保证不含敏感信息(实体黑板本来就经过
    # categorize → 我们的 redaction 在 extract 阶段已经完成)。
    conversation = list(redact_messages(messages, redaction_ctx))
    total_prompt = 0
    total_completion = 0
    timeout = timeout_seconds or 600  # tool calling 多轮,留长

    async def _one_round() -> Any:
        """单轮 acompletion + 重试(transient 网络错误)。"""
        backoffs = [int(s) for s in settings.llm_retry_backoff_s.split(",")]
        last: Exception | None = None
        for attempt in range(settings.llm_retry_max + 1):
            try:
                return await litellm.acompletion(
                    model=model,
                    messages=conversation,
                    api_key=api_key,
                    tools=tools,
                    tool_choice="auto",
                    stream=False,
                    **kw,
                )
            except (
                RateLimitError,
                ServiceUnavailableError,
                APIConnectionError,
                Timeout,
            ) as e:
                last = e
                log.warning(
                    "llm_tool_round_retry",
                    model=model,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < settings.llm_retry_max:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                await _write_llm_error(
                    project_id,
                    "LLM tool round exhausted",
                    model=model,
                    mode="tools",
                    total_attempts=attempt + 1,
                    last_error=str(e),
                )
                raise LLMRetryFailed(str(e)) from e
        raise LLMRetryFailed(str(last) if last else "unknown")

    try:
        async with asyncio.timeout(timeout):
            for round_idx in range(max_tool_rounds + 2):
                response = await _one_round()
                msg = response.choices[0].message
                usage = getattr(response, "usage", None)
                if usage is not None:
                    total_prompt += int(getattr(usage, "prompt_tokens", 0) or 0)
                    total_completion += int(
                        getattr(usage, "completion_tokens", 0) or 0
                    )
                tool_calls = getattr(msg, "tool_calls", None) or []

                if not tool_calls:
                    # —— 最终 content,解析 JSON 返回 ——
                    content = msg.content or "{}"
                    await record_token_usage(
                        user_id=user_id,
                        project_id=project_id,
                        run_id=run_id,
                        model=model,
                        prompt_tokens=total_prompt,
                        completion_tokens=total_completion,
                    )
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        # 容错:从混合输出里抽第一个 {...}
                        m = re.search(r"\{[\s\S]*\}", content)
                        if not m:
                            raise LLMRetryFailed(
                                f"tool-calling final answer not JSON: {content[:200]}"
                            ) from None
                        try:
                            parsed = json.loads(m.group())
                        except json.JSONDecodeError as je:
                            raise LLMRetryFailed(
                                f"tool-calling final json parse: {je}"
                            ) from je
                    return parsed, StreamResult(
                        text=content,
                        prompt_tokens=total_prompt,
                        completion_tokens=total_completion,
                    )

                # —— 仍在 tool 阶段;到 cap 后强制终止 ——
                if round_idx >= max_tool_rounds:
                    log.warning(
                        "llm_tool_loop_force_terminate",
                        model=model,
                        max_rounds=max_tool_rounds,
                    )
                    conversation.append(
                        {
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in tool_calls
                            ],
                        }
                    )
                    # 兜底回 dummy tool result + 系统催「输出最终答案」
                    for tc in tool_calls:
                        conversation.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(
                                    {
                                        "error": (
                                            "max tool rounds reached, "
                                            "no more tool calls allowed"
                                        )
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    conversation.append(
                        {
                            "role": "system",
                            "content": (
                                "已达到工具调用上限,请基于已收集的信息直接"
                                "输出最终 JSON 答案,不要再调用任何工具。"
                            ),
                        }
                    )
                    continue

                # —— 正常路径:把 tool_calls + tool result 入 conversation ——
                conversation.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
                for tc in tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        result_str = await tool_handler(fn_name, args)
                    except Exception as e:
                        log.exception(
                            "llm_tool_handler_failed",
                            tool=fn_name,
                            args=args,
                        )
                        result_str = json.dumps(
                            {"error": f"tool handler crashed: {e}"},
                            ensure_ascii=False,
                        )
                    if not isinstance(result_str, str):
                        result_str = json.dumps(result_str, ensure_ascii=False)
                    log.info(
                        "llm_tool_called",
                        round=round_idx,
                        tool=fn_name,
                        args=args,
                        result_chars=len(result_str),
                    )
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        }
                    )
                # 进入下一轮
    except TimeoutError as te:
        await _write_llm_error(
            project_id,
            "LLM tool loop total timeout",
            model=model,
            mode="tools",
            timeout_seconds=timeout,
        )
        raise LLMTimeoutExceeded(
            f"LLM tool loop exceeded {timeout}s total timeout"
        ) from te

    raise LLMRetryFailed("tool loop exhausted without final answer")


async def _fake_stream(
    model: str,
    messages: list[dict[str, Any]],
    project_id: int,
    chapter_index: int | None,
    on_partial: Any = None,
) -> StreamResult:
    """``BID_APP_FAKE_LLM=1`` 时用,不调外网(§18.2)。

    ⭐ R-14:同样支持 ``on_partial`` 回调,每 100 chars 触发一次,跑完
    一次 final flush——保持与真路径相同的 partial 落库节奏,前端 hydrate
    路径开发期间也能验证。
    """
    import time as _time

    fake = "# 章节标题\n\n这是测试用的章节正文。" + "占位段落。" * 50
    sent: list[str] = []
    last_flush = _time.monotonic()
    chars_since = 0
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
            sent.append(ch)
            chars_since += 1
            if on_partial is not None and (
                chars_since >= 100
                or (_time.monotonic() - last_flush) >= 1.0
            ):
                try:
                    await on_partial("".join(sent))
                except Exception:
                    log.exception("fake_stream_on_partial_failed")
                chars_since = 0
                last_flush = _time.monotonic()
    if on_partial is not None and chars_since > 0:
        try:
            await on_partial(fake)
        except Exception:
            log.exception("fake_stream_on_partial_final_failed")
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
