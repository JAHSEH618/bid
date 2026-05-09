"""最终 proposal Markdown 的 Humanizer-zh 润色步骤。"""

from __future__ import annotations

import re

import structlog

from ..config import settings
from ..services.llm import call_llm_stream
from .postprocess import postprocess_chapter_markdown
from .prompts.humanizer_zh_prompt import build_messages
from .resolve import resolve_api_key, resolve_models, resolve_user_id

log = structlog.get_logger()

_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<fence>```|~~~)")
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$"
)
_WRAPPING_MARKDOWN_FENCE_RE = re.compile(
    r"^[ \t]*```(?:markdown|md)?[ \t]*\n(?P<body>.*)\n```[ \t]*$",
    re.DOTALL,
)


def _make_token(index: int) -> str:
    return f"@@PROTECTED_BLOCK_{index:03d}@@"


def _protect_markdown_blocks(markdown: str) -> tuple[str, dict[str, str]]:
    """把 Mermaid / 代码块 / Markdown 表格替换成占位符,避免润色时被改坏。"""
    if not markdown:
        return markdown, {}

    lines = markdown.splitlines()
    out: list[str] = []
    blocks: dict[str, str] = {}
    i = 0

    while i < len(lines):
        line = lines[i]
        fence_match = _FENCE_OPEN_RE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            block = [line]
            i += 1
            while i < len(lines):
                block.append(lines[i])
                if re.match(rf"^[ \t]*{re.escape(fence)}[ \t]*$", lines[i]):
                    i += 1
                    break
                i += 1
            token = _make_token(len(blocks))
            blocks[token] = "\n".join(block)
            out.append(token)
            continue

        if _TABLE_ROW_RE.match(line):
            table_lines: list[str] = []
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            if len(table_lines) >= 2 and any(
                _TABLE_SEPARATOR_RE.match(table_line) for table_line in table_lines[1:3]
            ):
                token = _make_token(len(blocks))
                blocks[token] = "\n".join(table_lines)
                out.append(token)
            else:
                out.extend(table_lines)
            continue

        out.append(line)
        i += 1

    return "\n".join(out), blocks


def _restore_markdown_blocks(markdown: str, blocks: dict[str, str]) -> str:
    restored = markdown
    for token, block in blocks.items():
        restored = restored.replace(token, block)
    return restored


def _strip_wrapping_markdown_fence(markdown: str) -> str:
    match = _WRAPPING_MARKDOWN_FENCE_RE.match(markdown.strip())
    if not match:
        return markdown.strip()
    return match.group("body").strip()


async def humanize_final_proposal(
    *,
    project_id: int,
    run_id: int | None,
    proposal_md: str,
) -> str:
    """按 Humanizer-zh 规则最终润色全文。

    这是非关键增强步骤:失败或占位符丢失时返回原始 Markdown,避免破坏已通过
    人工审核的章节内容。
    """
    if not proposal_md.strip() or settings.bid_app_fake_llm:
        return proposal_md

    protected_md, protected_blocks = _protect_markdown_blocks(proposal_md)
    try:
        api_key = await resolve_api_key(project_id, run_id=run_id)
        user_id = await resolve_user_id(project_id)
        models = await resolve_models(project_id)
        result = await call_llm_stream(
            model=models.chapter_model,
            messages=build_messages(protected_md),
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            chapter_index=None,
            temperature=0.25,
            max_tokens=32768,
        )
    except Exception:
        log.exception("humanize_final_proposal_failed", project_id=project_id)
        return proposal_md

    polished = _strip_wrapping_markdown_fence(result.text)
    if not polished:
        log.warning("humanize_final_proposal_empty", project_id=project_id)
        return proposal_md

    missing_tokens = [token for token in protected_blocks if token not in polished]
    if missing_tokens:
        log.warning(
            "humanize_final_proposal_missing_protected_blocks",
            project_id=project_id,
            missing_count=len(missing_tokens),
        )
        return proposal_md

    restored = _restore_markdown_blocks(polished, protected_blocks)
    return postprocess_chapter_markdown(restored)
