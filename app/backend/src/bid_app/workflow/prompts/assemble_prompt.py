"""全文整合模板(移植 v10 §4.6 / 模板转换 ``assemble_proposal``)。

⚠️ 不调 LLM:这是 Loop 跑完后的纯 Jinja 字符串拼接,把 ``finalized_chapters``
数组拼成完整的投标方案 Markdown。仍按"prompts"目录归类,因为它属于
工作流提示词/模板族(节点 ``assemble`` 直接 import 用)。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ...config import settings

HEADER_TEMPLATE = """# 技术方案

> **章节总数**:{count} / {total}
> **生成时间**:{ts}

---

"""

FOOTER = "\n\n> *方案完*\n"

CHAPTER_SEPARATOR = "\n\n---\n\n"


def assemble_proposal(
    chapters_array: list[str],
    *,
    total_chapters: int | None = None,
) -> str:
    """把 ``finalized_chapters`` 数组拼成完整方案 markdown。

    与 v10 §4.6 Jinja 模板等价输出形态,但用 Python 字符串实现,避免引入
    Jinja 渲染依赖。``total_chapters`` 给提纲规划的总章节数(可能多于实际
    生成的,跳过/失败的占位也算 1 章)。
    """
    chapters_array = chapters_array or []
    if total_chapters is None:
        total_chapters = len(chapters_array)

    ts = datetime.now(ZoneInfo(settings.tz)).strftime("%Y-%m-%d %H:%M")
    header = HEADER_TEMPLATE.format(
        count=len(chapters_array),
        total=total_chapters,
        ts=ts,
    )
    body = CHAPTER_SEPARATOR.join(ch.rstrip() for ch in chapters_array)
    if body:
        body += "\n"
    return header + body + FOOTER
