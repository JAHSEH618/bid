"""章节合并节点(v10 §4.5.4 / Spec §10.2 ``merge_chapter``)。

把 LLM-2 正文 + LLM-3 可视化建议 JSON 拼成 ``full_chapter`` markdown,塞回
``state._pending_chapter_text``,供下游 ``human_review`` interrupt 把完整
markdown 推给前端预览。

⚠️ 本节点**仅**做模板转换,**不**调 interrupt;P5 人工审核走单独的
``human_review`` 节点(D-EE 拆分,§10.2 / §10.6b)。

D-EI (2026-05-18) Stage 4 校验器:模板转换完成后跑
``template_validator.validate_chapter``,把 issues 落到 state 临时载体
``_validation_issues``;同时算 ``_should_auto_revise`` 标志(命中 error +
``retry_count`` 还有额度)给路由器用。
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ...services.template_validator import validate_chapter
from ..postprocess import postprocess_chapter_markdown
from ..state import WorkflowState

log = structlog.get_logger()

_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<fence>```|~~~)[ \t]*(?P<lang>[\w-]+)?[^\n]*$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*(?P<fence>```|~~~)[ \t]*$")
_INLINE_FENCE_RE = re.compile(
    r"^[ \t]*(?:```|~~~)[ \t]*(?:[\w-]+)?[ \t]*(?P<body>.*?)[ \t]*(?:```|~~~)[ \t]*$",
    re.DOTALL,
)


def _strip_wrapping_fence(content: Any) -> str:
    """LLM-3 偶尔把 JSON content 写成 fenced block,这里去掉外层围栏。"""
    text = str(content or "").strip()
    if not text:
        return ""

    inline = _INLINE_FENCE_RE.match(text)
    if inline and "\n" not in text:
        return inline.group("body").strip()

    lines = text.splitlines()
    for start, line in enumerate(lines):
        open_match = _FENCE_OPEN_RE.match(line)
        if not open_match:
            continue
        fence = open_match.group("fence")
        for end in range(len(lines) - 1, start, -1):
            close_match = _FENCE_CLOSE_RE.match(lines[end])
            if close_match and close_match.group("fence") == fence:
                return "\n".join(lines[start + 1 : end]).strip()
        break
    return text


def _paragraph_bounds(text: str, index: int) -> tuple[int, int]:
    """返回包含 index 的 Markdown 段落边界。"""
    start = text.rfind("\n\n", 0, index)
    end = text.find("\n\n", index)
    return (0 if start == -1 else start + 2, len(text) if end == -1 else end)


def _append_markdown_block(text: str, block: str) -> str:
    parts = [p.strip() for p in (text, block) if p and p.strip()]
    return "\n\n".join(parts)


def _render_visual_block(item: dict[str, Any], index: int) -> str:
    v_type = item["type"]
    label = "表" if v_type == "table" else "图"
    title = item.get("title") or f"可视化 {index}"
    lines = [f"#### {label} {index}: {title}", ""]
    if v_type == "mermaid":
        lines.extend(["```mermaid", item["content"], "```"])
    else:
        lines.append(item["content"])
    return "\n".join(lines).strip()


def _insert_visual_blocks(chapter_text: str, renderable_items: list[dict[str, Any]]) -> str:
    """按 LLM-3 的 anchor / position 把图表插回正文,找不到锚点则章末兜底。"""
    body = chapter_text.strip()
    if not body or not renderable_items:
        return body

    operations: list[dict[str, Any]] = []
    fallback_blocks: list[str] = []
    replace_ranges: list[tuple[int, int]] = []

    for order, item in enumerate(renderable_items):
        block = _render_visual_block(item, order + 1)
        anchor = str(item.get("anchor") or "").strip()
        anchor_index = body.find(anchor) if anchor else -1
        if anchor_index == -1:
            log.warning(
                "merge_chapter_visual_anchor_not_found",
                anchor=anchor,
                title=item.get("title", ""),
            )
            fallback_blocks.append(block)
            continue

        paragraph_start, paragraph_end = _paragraph_bounds(body, anchor_index)
        position = str(item.get("position") or "after").strip().lower()
        if position == "before":
            start = end = paragraph_start
        elif position == "replace":
            start, end = paragraph_start, paragraph_end
        else:
            start = end = paragraph_end

        if start != end and any(
            not (end <= existing_start or start >= existing_end)
            for existing_start, existing_end in replace_ranges
        ):
            log.warning(
                "merge_chapter_visual_replace_overlap_ignored",
                anchor=anchor,
                title=item.get("title", ""),
            )
            fallback_blocks.append(block)
            continue

        if start != end:
            replace_ranges.append((start, end))
        operations.append(
            {
                "start": start,
                "end": end,
                "block": block,
                "order": order,
            }
        )

    result = body
    for op in sorted(operations, key=lambda item: (item["start"], item["order"]), reverse=True):
        result = _append_markdown_block(
            result[: op["start"]],
            _append_markdown_block(op["block"], result[op["end"] :]),
        )

    for block in fallback_blocks:
        result = _append_markdown_block(result, block)

    return result


def _render_full_chapter(
    *,
    chapter_index: int,
    chapter_title: str,
    chapter_text: str,
    visuals_json_str: str,
) -> str:
    """v10 §4.5.4 模板转换的 Python 等价实现。"""
    items: list[dict[str, Any]] = []
    try:
        loaded = json.loads(visuals_json_str or "{}")
        if isinstance(loaded, dict):
            raw_items = loaded.get("items") or []
            if isinstance(raw_items, list):
                items = [item for item in raw_items if isinstance(item, dict)]
    except json.JSONDecodeError:
        log.warning(
            "merge_chapter_visuals_json_invalid",
            head=(visuals_json_str or "")[:120],
        )

    renderable_items: list[dict[str, Any]] = []
    for v in items:
        v_type = str(v.get("type", "mermaid")).strip().lower()
        v_content = _strip_wrapping_fence(v.get("content", ""))
        if not v_content:
            continue
        if v_type not in {"mermaid", "table"}:
            log.warning(
                "merge_chapter_visual_type_ignored",
                visual_type=v_type,
                title=v.get("title", ""),
            )
            continue
        renderable_items.append({**v, "type": v_type, "content": v_content})

    merged_body = _insert_visual_blocks(chapter_text, renderable_items)
    parts: list[str] = [
        f"## 第 {chapter_index + 1} 章 · {chapter_title}",
        "",
        merged_body,
        "",
    ]

    return postprocess_chapter_markdown("\n".join(parts).rstrip() + "\n")


async def run(state: WorkflowState) -> dict[str, Any]:
    idx = state["current_index"]
    chapter = state["chapters"][idx]

    full_chapter = _render_full_chapter(
        chapter_index=idx,
        chapter_title=chapter.get("title", ""),
        chapter_text=state.get("_pending_chapter_text", ""),
        visuals_json_str=state.get("_pending_visuals_json", '{"items": []}'),
    )

    # D-EI Stage 4:跑结构化校验器,把 issues 落 state;命中 error 且 retry 额度
    # 未用完 → _should_auto_revise=True,graph 条件边据此决定回 write_chapter
    # 重试 or 进 human_review。
    issues = validate_chapter(full_chapter, chapter)
    error_count = sum(1 for i in issues if i.severity == "error")
    retry_count = int(state.get("retry_count", 0) or 0)
    max_retry = int(state.get("max_retry_per_chapter", 3) or 3)
    # 自动重试硬上限 1 次:超过用户给的 max_retry 也强制停,避免死循环。
    auto_revise_cap = max(1, min(1, max_retry))
    should_auto_revise = error_count > 0 and retry_count < auto_revise_cap

    if issues:
        log.info(
            "merge_chapter_validation_issues",
            project_id=state.get("project_id"),
            chapter_index=idx,
            chapter_type=chapter.get("chapter_type"),
            errors=error_count,
            warnings=len(issues) - error_count,
            auto_revise=should_auto_revise,
        )

    return {
        "_pending_chapter_text": full_chapter,
        "_validation_issues": [i.to_dict() for i in issues],
        "_should_auto_revise": should_auto_revise,
    }
