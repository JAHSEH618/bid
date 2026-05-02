"""三模型 smoke CLI(§22 M0 Day1)。

用法::

    python -m bid_app.cli.test_llm --api-key sk-xxx
    python -m bid_app.cli.test_llm --api-key sk-xxx --model dashscope/qwen3.6-max-preview

逐一打 LLM-1 / LLM-2 / LLM-3,要求每个模型至少返回 100 chars。
"""
from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

from ..config import settings
from ..services.llm import call_llm_json, call_llm_stream

console = Console()


SMOKE_MESSAGES_OUTLINE = [
    {
        "role": "system",
        "content": "你是一个返回 JSON 的助理。仅输出 JSON 对象,不要任何解释。",
    },
    {
        "role": "user",
        "content": (
            '请输出 JSON {"chapters": [{"id":"ch_01","title":"概述",'
            '"summary":"项目背景一段","key_points":["..."],"target_pages":2,'
            '"matched_scoring_items":["1.1"]}]}。仅输出该 JSON,不要 markdown 代码块。'
        ),
    },
]

SMOKE_MESSAGES_CHAPTER = [
    {
        "role": "system",
        "content": "你是一个投标技术方案章节撰写助理。",
    },
    {
        "role": "user",
        "content": (
            "请写一段 200 字以上的概述文字,主题:某地业务系统建设的技术目标与价值。"
            "用中文 markdown,只回正文,不要 ```fence。"
        ),
    },
]

SMOKE_MESSAGES_VISUALS = [
    {
        "role": "system",
        "content": "你是一个返回 JSON 的助理。仅输出 JSON。",
    },
    {
        "role": "user",
        "content": (
            '请输出 JSON {"diagrams":[{"type":"flowchart","caption":"流程",'
            '"mermaid":"flowchart TD; A-->B; B-->C"}]}。仅输出 JSON。'
        ),
    },
]


async def _smoke_one(
    label: str,
    model: str,
    api_key: str,
    *,
    stream: bool,
) -> tuple[bool, int, str]:
    try:
        if stream:
            res = await call_llm_stream(
                model=model,
                messages=SMOKE_MESSAGES_CHAPTER,
                api_key=api_key,
                user_id=0,
                project_id=0,
                chapter_index=None,
                temperature=0.3,
            )
            text = res.text
        else:
            messages = (
                SMOKE_MESSAGES_OUTLINE if label == "LLM-1" else SMOKE_MESSAGES_VISUALS
            )
            _parsed, sr = await call_llm_json(
                model=model,
                messages=messages,
                api_key=api_key,
                user_id=0,
                project_id=0,
                timeout_seconds=120,
            )
            text = sr.text
        ok = len(text) >= 100
        return ok, len(text), text[:120].replace("\n", " ")
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {e}"


@click.command()
@click.option(
    "--api-key",
    "api_key",
    required=True,
    help="DashScope API Key(sk-xxx)",
    envvar="DASHSCOPE_API_KEY",
)
@click.option(
    "--model",
    "model_override",
    default=None,
    help="覆盖测试单一模型(默认三模型全打)",
)
def main(api_key: str, model_override: str | None) -> None:
    """三模型 smoke。每个模型至少返回 100 chars 才算通过。"""
    if model_override:
        targets: list[tuple[str, str, bool]] = [(model_override, model_override, True)]
    else:
        targets = [
            ("LLM-1", settings.llm1_outline_model, False),  # JSON 模式
            ("LLM-2", settings.llm2_chapter_model, True),  # 流式
            ("LLM-3", settings.llm3_visuals_model, False),  # JSON 模式
        ]

    console.print("[bold]三模型 smoke 开始...[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Label")
    table.add_column("Model")
    table.add_column("Mode")
    table.add_column("OK")
    table.add_column("Chars")
    table.add_column("Preview")

    failed = 0
    for label, model, stream in targets:
        ok, n, preview = asyncio.run(_smoke_one(label, model, api_key, stream=stream))
        if not ok:
            failed += 1
        table.add_row(
            label,
            model,
            "stream" if stream else "json",
            "yes" if ok else "no",
            str(n),
            preview,
        )

    console.print(table)
    if failed:
        console.print(f"[red]失败 {failed}/{len(targets)},检查 API Key 与模型名[/red]")
        sys.exit(2)
    console.print("[green]三模型 smoke 通过[/green]")


if __name__ == "__main__":
    main()
