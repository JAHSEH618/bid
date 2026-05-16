"""PoC: DashScope tool calling 可靠性测试 (Phase 2 前置验证)。

跑法:
    cd app/backend
    DASHSCOPE_API_KEY=sk-xxx uv run python -m tests.poc_tool_calling

目的:
- 验证 deepseek-v4-pro / qwen3.6-max 在 DashScope 走 LiteLLM 时,
  tool calling 是否可靠 (能正确发起 tool_call、接收结果、继续生成)
- 统计 N 轮调用里:
    * tool_call_emitted: 模型是否 emit 了 tool_calls
    * tool_name_correct: 调用的工具名是否在我们定义的集合里 (vs 幻觉)
    * tool_args_parseable: arguments JSON 能解析
    * continuation_used_result: 模型最终回答里是否引用了 tool 返回结果
- 输出每个模型一份计数表;若 deepseek-v4-pro 召回率 ≥ 80% 即可推进 Phase 2

不放进 pytest:它需要真 API key + 真网络 + 烧 token,只手工跑。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import litellm

MODELS_TO_TEST = [
    "dashscope/deepseek-v4-pro",
    "dashscope/qwen3.6-max-preview",
]

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_blackboard",
            "description": (
                "从招标项目的实体黑板里检索相关条目。当你需要查找"
                "招标方信息、评分细则、技术要求、人员资质、风险条款等"
                "信息时主动调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "project_info",
                                "company_info",
                                "personnel_info",
                                "scoring_rules",
                                "technical_requirements",
                                "qualification_requirements",
                                "timeline_constraints",
                                "commercial_terms",
                                "compliance_constraints",
                                "risk_signals",
                            ],
                        },
                        "description": "要检索的实体桶类型",
                    },
                    "query": {
                        "type": "string",
                        "description": "关键字查询;留空则返回该桶全部",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回前 K 条,默认 5",
                        "default": 5,
                    },
                },
                "required": ["entity_types"],
            },
        },
    },
]

VALID_TOOL_NAMES = {"search_blackboard"}
VALID_ENTITY_TYPES = {
    "project_info",
    "company_info",
    "personnel_info",
    "scoring_rules",
    "technical_requirements",
    "qualification_requirements",
    "timeline_constraints",
    "commercial_terms",
    "compliance_constraints",
    "risk_signals",
}

# 模拟黑板返回:第一次调用给「风险管控」相关条目
FAKE_BLACKBOARD_RESPONSE = [
    {
        "tags": ["risk_signals"],
        "section": "评分规则附件 B",
        "content": "风险管控章节缺失 → 扣 8 分;无量化指标 → 再扣 3 分。",
    },
    {
        "tags": ["compliance_constraints", "risk_signals"],
        "section": "甲方合同模板第 4 章",
        "content": "投标人须在 24 小时内响应任何 P1 故障,否则违约金 5 万/次。",
    },
    {
        "tags": ["technical_requirements"],
        "section": "技术需求 §7.2",
        "content": "系统可用性 SLA ≥ 99.95%;RTO ≤ 4h,RPO ≤ 30min。",
    },
]

SYSTEM_PROMPT = """你是投标技术方案撰写专家。你正在撰写技术方案的某一章节。

你可以调用 `search_blackboard(entity_types, query)` 检索项目实体黑板。
当你需要任何关于招标方、评分规则、技术要求、风险条款的具体细节时,
**必须**调用此工具,不要凭空编造。

写作要求:
- 内容紧扣招标材料,不能空泛
- 引用具体条款、量化指标
"""

USER_PROMPT = """请为本方案撰写 **"3.2 风险管控体系"** 一节,目标 3 页,约 2400 字。

要求:
- 覆盖评分规则对本节的要求(必查)
- 引用具体 SLA / 响应时效条款(必查)
- 列出至少 5 个量化风险指标

请先检索黑板获取必要信息,再撰写。
"""


def mock_tool_call(call_args: dict[str, Any]) -> str:
    """模拟 search_blackboard 的返回。entity_types 与 risk/compliance 沾边
    就返回 FAKE_BLACKBOARD_RESPONSE,否则返回 []。"""
    types = set(call_args.get("entity_types") or [])
    if types & {"risk_signals", "compliance_constraints", "technical_requirements", "scoring_rules"}:
        return json.dumps(FAKE_BLACKBOARD_RESPONSE, ensure_ascii=False, indent=2)
    return json.dumps([], ensure_ascii=False)


async def run_one_round(model: str, api_key: str, round_idx: int) -> dict[str, Any]:
    """跑一轮:发起 → tool_call → 结果回灌 → 终稿。返回统计字段。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]

    stats = {
        "model": model,
        "round": round_idx,
        "tool_call_emitted": False,
        "tool_name_correct": False,
        "tool_args_parseable": False,
        "tool_entity_types_valid": False,
        "continuation_received": False,
        "continuation_chars": 0,
        "continuation_used_result": False,
        "error": None,
    }

    try:
        # 第 1 步:发起 — 期望 tool_call
        resp = await litellm.acompletion(
            model=model,
            messages=messages,
            api_key=api_key,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2048,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            stats["error"] = "no tool_calls emitted on first turn"
            return stats

        stats["tool_call_emitted"] = True
        tc = tool_calls[0]
        fn_name = tc.function.name
        fn_args_raw = tc.function.arguments

        stats["tool_name_correct"] = fn_name in VALID_TOOL_NAMES

        try:
            fn_args = json.loads(fn_args_raw)
            stats["tool_args_parseable"] = True
            types_arg = fn_args.get("entity_types") or []
            stats["tool_entity_types_valid"] = (
                isinstance(types_arg, list)
                and len(types_arg) > 0
                and all(t in VALID_ENTITY_TYPES for t in types_arg)
            )
        except json.JSONDecodeError:
            stats["error"] = f"tool arguments not JSON: {fn_args_raw[:200]}"
            return stats

        if not stats["tool_name_correct"]:
            stats["error"] = f"hallucinated tool name: {fn_name}"
            return stats

        # 第 2 步:把 tool 结果回灌
        tool_result = mock_tool_call(fn_args)
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": fn_args_raw,
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result,
            }
        )

        # 第 3 步:让模型继续 — 期望它根据 tool 结果写正文
        resp2 = await litellm.acompletion(
            model=model,
            messages=messages,
            api_key=api_key,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=4096,
        )
        msg2 = resp2.choices[0].message
        content2 = msg2.content or ""
        stats["continuation_received"] = bool(content2.strip())
        stats["continuation_chars"] = len(content2)
        # 粗略判定模型是否引用了 tool 结果:正文里出现 "99.95%"/"RTO"/"违约金"/"8 分" 之一
        markers = ["99.95", "RTO", "RPO", "违约金", "P1", "8 分", "8分"]
        stats["continuation_used_result"] = any(m in content2 for m in markers)

    except Exception as e:
        stats["error"] = f"{type(e).__name__}: {e}"
    return stats


async def main() -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get(
        "BID_APP_CLI_API_KEY"
    )
    if not api_key:
        raise SystemExit(
            "set DASHSCOPE_API_KEY=sk-xxx (or BID_APP_CLI_API_KEY) before running"
        )

    rounds_per_model = int(os.environ.get("POC_ROUNDS", "5"))
    print(
        f"== Tool calling PoC == ({rounds_per_model} rounds/model)"
        f"  models: {MODELS_TO_TEST}\n"
    )

    all_results: dict[str, list[dict[str, Any]]] = {}
    for model in MODELS_TO_TEST:
        print(f"\n--- {model} ---")
        results: list[dict[str, Any]] = []
        for i in range(rounds_per_model):
            r = await run_one_round(model, api_key, i + 1)
            results.append(r)
            err = r.get("error") or "ok"
            print(
                f"  round {r['round']}: "
                f"emit={r['tool_call_emitted']} "
                f"name={r['tool_name_correct']} "
                f"args={r['tool_args_parseable']} "
                f"types={r['tool_entity_types_valid']} "
                f"cont={r['continuation_received']} "
                f"used={r['continuation_used_result']} "
                f"chars={r['continuation_chars']} "
                f"err={err if err != 'ok' else '-'}"
            )
        all_results[model] = results

    print("\n== Summary ==")
    for model, results in all_results.items():
        n = len(results)
        if n == 0:
            continue
        emit_rate = sum(r["tool_call_emitted"] for r in results) / n
        name_rate = sum(r["tool_name_correct"] for r in results) / n
        args_rate = sum(r["tool_args_parseable"] for r in results) / n
        types_rate = sum(r["tool_entity_types_valid"] for r in results) / n
        cont_rate = sum(r["continuation_received"] for r in results) / n
        used_rate = sum(r["continuation_used_result"] for r in results) / n
        print(
            f"  {model:40s} "
            f"emit={emit_rate:.0%} "
            f"name={name_rate:.0%} "
            f"args={args_rate:.0%} "
            f"types={types_rate:.0%} "
            f"cont={cont_rate:.0%} "
            f"used_result={used_rate:.0%}"
        )


if __name__ == "__main__":
    asyncio.run(main())
