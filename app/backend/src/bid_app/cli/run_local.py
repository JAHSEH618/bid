"""M0 CLI — 命令行交互式审核(§22 D-DZ / §7.3)。

用法::

    cd app/backend
    uv run python -m bid_app.cli.run_local \\
        --tech-spec ./samples/tech_spec.docx \\
        --scoring  ./samples/scoring.docx \\
        --template ./samples/template.docx \\
        --api-key  sk-xxx \\
        --pages-per-chapter 3 \\
        --out      ./out

行为:
1. 读取 3 份输入文档,markitdown 抽取
2. 构造 WorkflowState,**在 CLI 进程内本地跑** LangGraph(无 PostgresSaver,
   用 LangGraph 默认 in-memory checkpointer)
3. 在 outline_review / merge_chapter 两个 interrupt 节点上 prompt 用户
4. 跑完后 ``proposal.md`` + ``proposal.smoke.docx`` 写到 ``--out`` 目录

⚠️ 本 CLI 不写 DB / 不发 SSE(``sync_chapter_to_db`` / ``publish_event``
都在 except 路径吞掉)。M0 只验证 LLM + LangGraph + Pandoc smoke 链路。

D-DZ 验收口径:markdown 字数 ≥ 5000;``smoke.docx`` 能用 Word 打开。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import click
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from ..services.document_extractor import extract_files
from ..services.docx_export import export_docx_smoke
from ..workflow.graph import build_graph
from ..workflow.state import WorkflowState

console = Console()


def _print_outline(chapters: list[dict[str, Any]]) -> None:
    console.rule("[bold]LLM-1 提纲(请审核)[/bold]")
    for i, ch in enumerate(chapters):
        console.print(
            f"[bold cyan]{i + 1:02d}.[/bold cyan] "
            f"[bold]{ch.get('title', '')}[/bold]  "
            f"([dim]{ch.get('target_pages', '?')} pages[/dim])"
        )
        if ch.get("summary"):
            console.print(f"     {ch['summary']}", style="dim")
        for kp in ch.get("key_points") or []:
            console.print(f"       • {kp}", style="dim")


def _prompt_outline_decision(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    _print_outline(chapters)
    confirm = Prompt.ask(
        "[bold]是否使用此提纲?[/bold]",
        choices=["y", "n"],
        default="y",
    )
    if confirm == "y":
        return {"chapters": []}  # 空 list / None 表示自动确认
    console.print("(暂不支持 CLI 行内编辑;请直接重启并修改文档后重跑)")
    sys.exit(2)


def _prompt_chapter_review(
    chapter_index: int, chapter_text: str
) -> dict[str, Any]:
    console.rule(f"[bold]章节 {chapter_index + 1} · 人工审核[/bold]")
    console.print(Panel(Markdown(chapter_text or "(空)")))
    decision = Prompt.ask(
        "[bold]决策[/bold] (a=approve / r=revise / s=skip)",
        choices=["a", "r", "s"],
        default="a",
    )
    decision_map = {"a": "approve", "r": "revise", "s": "skip"}
    out: dict[str, Any] = {"decision": decision_map[decision]}
    if decision == "r":
        feedback = Prompt.ask("[bold]修改建议[/bold] (一行;Ctrl+C 退出)")
        out["feedback"] = feedback
    return out


async def _run_async(args: dict[str, Any]) -> int:
    out_dir: Path = args["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print("[bold]1. 抽取 3 份输入文档...[/bold]")
    docs = extract_files(
        tech_spec=args["tech_spec"],
        scoring=args["scoring"],
        template=args["template"],
    )
    console.print(
        f"   tech_spec  {len(docs['tech_spec_md'])} chars\n"
        f"   scoring    {len(docs['scoring_md'])} chars\n"
        f"   template   {len(docs['template_md'])} chars"
    )

    # CLI 走 fake project_id;workflow 节点中所有 DB 写入在异常时被吞掉
    project_id_fake = -1
    run_id_fake = -1

    initial: WorkflowState = {
        "project_id": project_id_fake,
        "run_id": run_id_fake,
        "tech_spec_md": docs["tech_spec_md"],
        "scoring_md": docs["scoring_md"],
        "template_md": docs["template_md"],
        "pages_per_chapter": int(args["pages_per_chapter"]),
        "max_retry_per_chapter": int(args["max_retry"]),
        "chapters": [],
        "current_index": 0,
        "retry_count": 0,
        "finalized_chapters": [],
        "revision_feedback": "",
    }

    console.print("[bold]2. 构建 LangGraph(无 PostgresSaver)...[/bold]")
    graph = build_graph(checkpointer=None)
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    console.print("[bold]3. 启动工作流...[/bold]")
    state: dict[str, Any] = await graph.ainvoke(initial, config)

    # interrupt 后 graph.ainvoke 会带 __interrupt__ 信息返回;循环 resume
    while state.get("__interrupt__"):
        interrupts = state["__interrupt__"]
        if not interrupts:
            break
        # LangGraph 0.6+ 用 list[Interrupt];取第一个
        first = interrupts[0]
        payload = getattr(first, "value", None) or first
        kind = (payload or {}).get("kind") if isinstance(payload, dict) else None

        if kind == "outline_confirm":
            chapters = (payload or {}).get("current_chapters") or []
            resume_value = _prompt_outline_decision(chapters)
        elif kind == "chapter_review":
            chapter_text = (payload or {}).get("chapter_text", "")
            chapter_index = (payload or {}).get("chapter_index", 0)
            resume_value = _prompt_chapter_review(chapter_index, chapter_text)
        else:
            console.print(f"[red]未知 interrupt:{payload!r}[/red]")
            return 3

        state = await graph.ainvoke(Command(resume=resume_value), config)

    final_md = state.get("final_proposal") or ""
    if not final_md:
        console.print("[red]final_proposal 为空,工作流可能未跑完[/red]")
        return 4

    console.rule("[bold]4. 写出产物[/bold]")
    md_path = out_dir / "proposal.md"
    md_path.write_text(final_md, encoding="utf-8")
    console.print(f"   ✓ {md_path} ({len(final_md)} chars)")

    try:
        docx_path = await export_docx_smoke(
            markdown=final_md,
            project_dir=out_dir,
            output_name="proposal.smoke.docx",
        )
        console.print(f"   ✓ {docx_path}")
    except Exception as e:
        console.print(f"   [yellow]docx 导出失败(M0 smoke 容忍):{e}[/yellow]")

    if len(final_md) < 5000:
        console.print(
            f"[yellow]⚠️ markdown 字数 {len(final_md)} < 5000(D-DZ 验收口径)"
            f"[/yellow]"
        )
    return 0


@click.command()
@click.option("--tech-spec", "tech_spec", required=True, type=click.Path(exists=True))
@click.option("--scoring", "scoring", required=True, type=click.Path(exists=True))
@click.option("--template", "template", required=True, type=click.Path(exists=True))
@click.option("--api-key", "api_key", required=True, envvar="DASHSCOPE_API_KEY")
@click.option("--out", "out_dir", required=True, type=click.Path())
@click.option("--pages-per-chapter", default=3, type=int)
@click.option("--max-retry", default=3, type=int)
@click.option(
    "--fake-llm",
    is_flag=True,
    help="设 BID_APP_FAKE_LLM=1,跳过真 LLM 调用(用占位文本)",
)
def main(
    tech_spec: str,
    scoring: str,
    template: str,
    api_key: str,
    out_dir: str,
    pages_per_chapter: int,
    max_retry: int,
    fake_llm: bool,
) -> None:
    """M0 CLI 入口。"""
    if fake_llm:
        os.environ["BID_APP_FAKE_LLM"] = "1"
        console.print("[yellow](BID_APP_FAKE_LLM=1)[/yellow]")

    # 把 api_key 注入环境(write_chapter / generate_outline 节点会从
    # Project.encrypted_api_key_snapshot 取真实 key,M0 CLI 走的是"无 DB"路径,
    # 所以把 api_key 透传给 services/llm.py 调用方,需在 CLI 模式下覆盖
    # _resolve_api_key)。最简单:让 workflow 节点的 _resolve_api_key 在异常时
    # fallback 到 env var BID_APP_CLI_API_KEY。
    os.environ["BID_APP_CLI_API_KEY"] = api_key

    args = {
        "tech_spec": tech_spec,
        "scoring": scoring,
        "template": template,
        "out_dir": Path(out_dir),
        "pages_per_chapter": pages_per_chapter,
        "max_retry": max_retry,
    }
    code = asyncio.run(_run_async(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
