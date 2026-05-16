"""Phase 2B: call_llm_with_tools_json loop behavior tests.

Mock litellm.acompletion to verify:
- Single-round (no tool_calls) → returns JSON immediately
- Multi-round (tool_calls → tool result → final) → loop converges
- max_tool_rounds cap → force termination instruction sent
- Malformed JSON final → tries to extract, then LLMRetryFailed
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from bid_app.services.llm import (
    LLMRetryFailed,
    call_llm_with_tools_json,
)


def _make_response(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> SimpleNamespace:
    """构造一个最小 LiteLLM acompletion 返回的 namespace mock。"""
    tcs: list[SimpleNamespace] = []
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            tcs.append(
                SimpleNamespace(
                    id=tc.get("id", f"call_{i}"),
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=tc.get("arguments", "{}"),
                    ),
                )
            )
    msg = SimpleNamespace(
        content=content,
        tool_calls=tcs if tcs else None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


async def _dummy_handler(_name: str, _args: dict[str, Any]) -> str:
    return json.dumps({"hits": [{"content": "mock entry"}], "count": 1})


@pytest.mark.asyncio
async def test_tool_loop_returns_json_immediately_when_no_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 不调工具直接输出 JSON → 一轮完事。"""
    call_count = {"n": 0}

    async def mock_acompletion(**_kw: Any) -> Any:
        call_count["n"] += 1
        return _make_response(content='{"toc": [{"title": "ok"}]}')

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)
    monkeypatch.setattr(
        "bid_app.services.llm.record_token_usage",
        lambda **kw: _noop_async(),  # 通过 token usage 写入
    )

    parsed, sr = await call_llm_with_tools_json(
        model="dashscope/test",
        messages=[{"role": "user", "content": "hi"}],
        api_key="sk-test",
        user_id=0,
        project_id=-1,
        tools=[{"type": "function", "function": {"name": "x"}}],
        tool_handler=_dummy_handler,
        max_tool_rounds=4,
    )
    assert call_count["n"] == 1
    assert parsed == {"toc": [{"title": "ok"}]}
    assert sr.prompt_tokens == 10
    assert sr.completion_tokens == 20


@pytest.mark.asyncio
async def test_tool_loop_iterates_then_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 调 1 次工具 → 拿结果 → 第 2 轮给 JSON,共 2 轮,token 累加。"""
    rounds: list[Any] = [
        _make_response(
            tool_calls=[
                {
                    "name": "search_blackboard",
                    "arguments": json.dumps(
                        {"entity_types": ["scoring_rules"], "query": "权重"}
                    ),
                }
            ],
            prompt_tokens=15,
            completion_tokens=5,
        ),
        _make_response(
            content='{"toc": [{"title": "final"}]}',
            prompt_tokens=80,  # tool 结果让 prompt 涨了
            completion_tokens=40,
        ),
    ]
    handler_calls: list[tuple[str, dict[str, Any]]] = []

    async def mock_acompletion(**_kw: Any) -> Any:
        return rounds.pop(0)

    async def tracking_handler(name: str, args: dict[str, Any]) -> str:
        handler_calls.append((name, args))
        return json.dumps({"hits": [{"content": "权重 50%"}]})

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)
    monkeypatch.setattr(
        "bid_app.services.llm.record_token_usage",
        lambda **kw: _noop_async(),
    )

    parsed, sr = await call_llm_with_tools_json(
        model="dashscope/test",
        messages=[{"role": "user", "content": "hi"}],
        api_key="sk-test",
        user_id=0,
        project_id=-1,
        tools=[{"type": "function", "function": {"name": "search_blackboard"}}],
        tool_handler=tracking_handler,
        max_tool_rounds=4,
    )
    assert parsed["toc"][0]["title"] == "final"
    # handler 被调用了 1 次,参数解析正确
    assert len(handler_calls) == 1
    assert handler_calls[0][0] == "search_blackboard"
    assert handler_calls[0][1]["entity_types"] == ["scoring_rules"]
    # token 累加
    assert sr.prompt_tokens == 15 + 80
    assert sr.completion_tokens == 5 + 40


@pytest.mark.asyncio
async def test_tool_loop_extracts_json_from_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 最终回答里 JSON 前后有解释性文字 → 抽出来仍能用。"""
    content = (
        "好的,我已经检索完毕,目录如下:\n\n"
        '{"toc": [{"title": "X"}]}\n\n'
        "希望对你有帮助。"
    )

    async def mock_acompletion(**_kw: Any) -> Any:
        return _make_response(content=content)

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)
    monkeypatch.setattr(
        "bid_app.services.llm.record_token_usage",
        lambda **kw: _noop_async(),
    )

    parsed, _ = await call_llm_with_tools_json(
        model="dashscope/test",
        messages=[{"role": "user", "content": "hi"}],
        api_key="sk-test",
        user_id=0,
        project_id=-1,
        tools=[{"type": "function", "function": {"name": "x"}}],
        tool_handler=_dummy_handler,
        max_tool_rounds=4,
    )
    assert parsed == {"toc": [{"title": "X"}]}


@pytest.mark.asyncio
async def test_tool_loop_final_no_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_acompletion(**_kw: Any) -> Any:
        return _make_response(content="完全不是 JSON 也无大括号")

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)
    monkeypatch.setattr(
        "bid_app.services.llm.record_token_usage",
        lambda **kw: _noop_async(),
    )
    monkeypatch.setattr(
        "bid_app.services.llm._write_llm_error",
        lambda *a, **kw: _noop_async(),
    )

    with pytest.raises(LLMRetryFailed):
        await call_llm_with_tools_json(
            model="dashscope/test",
            messages=[{"role": "user", "content": "hi"}],
            api_key="sk-test",
            user_id=0,
            project_id=-1,
            tools=[{"type": "function", "function": {"name": "x"}}],
            tool_handler=_dummy_handler,
            max_tool_rounds=2,
        )


@pytest.mark.asyncio
async def test_tool_loop_caps_at_max_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 死循环调工具 → 到 cap 后注入「stop」system,再不收手就 RetryFailed。"""
    # 总是返 tool_calls,永不收手
    def make_round() -> Any:
        return _make_response(
            tool_calls=[
                {
                    "name": "search_blackboard",
                    "arguments": json.dumps({"entity_types": ["scoring_rules"]}),
                }
            ]
        )

    async def mock_acompletion(**_kw: Any) -> Any:
        return make_round()

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)
    monkeypatch.setattr(
        "bid_app.services.llm.record_token_usage",
        lambda **kw: _noop_async(),
    )
    monkeypatch.setattr(
        "bid_app.services.llm._write_llm_error",
        lambda *a, **kw: _noop_async(),
    )

    with pytest.raises(LLMRetryFailed):
        await call_llm_with_tools_json(
            model="dashscope/test",
            messages=[{"role": "user", "content": "hi"}],
            api_key="sk-test",
            user_id=0,
            project_id=-1,
            tools=[{"type": "function", "function": {"name": "search_blackboard"}}],
            tool_handler=_dummy_handler,
            max_tool_rounds=2,
        )


async def _noop_async() -> None:
    return None
