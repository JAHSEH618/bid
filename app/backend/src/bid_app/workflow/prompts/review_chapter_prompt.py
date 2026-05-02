"""LLM-3 提示词 — 章节可视化建议生成(移植 v10 §4.5.3)。

⚠️ 命名说明:任务清单把本文件标作 "review_chapter_prompt"(章节审核),
但 v10 §4.5.3 的实际职责是**可视化建议**——LLM-3 阅读 LLM-2 输出的章节正文,
识别哪些位置适合插图/插表/插流程图,输出 JSON 建议清单供下游 ``merge_chapter``
模板转换合并。文件名保留与任务清单一致,内容严格对齐 §4.5.3。

模型:``settings.llm3_visuals_model``(默认 dashscope/qwen3.6-flash)。
温度:0.4(略低保证 JSON 稳定);Response Format:JSON Object;Max Tokens 4096。
"""
from __future__ import annotations

from typing import Any

LLM3_SYSTEM = """你是一位技术文档可视化设计师,擅长识别长文本中适合用图表、流程图、表格、ASCII 示意图增强表达的位置。

你的任务:阅读用户提供的一段章节正文(Markdown 格式),输出一份 JSON 格式的可视化建议清单,\
标明每处建议在原文哪个位置插入、用什么类型、内容是什么。

可选可视化类型(type 字段):
- mermaid:流程图、时序图、甘特图、架构图(用 Mermaid 语法)
- table:Markdown 表格(对比、参数、矩阵)
- ascii:简单的 ASCII 框图(分层结构、模块关系)

输出规则:
1. 严格 JSON,不要任何前后缀文字、不要 markdown 代码块包裹
2. 每章建议 0-4 处可视化,质量优先于数量,无合适处则返回空数组
3. anchor 字段填一个能在原文中唯一定位的关键短语(8 字以内),不要填整段
4. position 取值:before(锚点前) / after(锚点后) / replace(替换锚点段落)
5. mermaid / ascii 的 content 必须是合法语法,直接可渲染
6. 不要建议给小标题、引言、总结段配图,只在确实能增强信息密度的位置建议
"""


LLM3_USER_TEMPLATE = """请阅读以下章节正文,识别其中适合配图/配表的位置,输出可视化建议 JSON。

章节标题:{chapter_title}

章节正文:
================================
{chapter_body_md}
================================

请严格按以下 schema 输出 JSON,不要任何其他文字:

{{
  "items": [
    {{
      "title": "可视化标题(简洁)",
      "type": "mermaid",
      "anchor": "原文中的关键短语",
      "position": "after",
      "content": "Mermaid/Markdown表格/ASCII 的实际内容"
    }}
  ]
}}

约束:
- items 数组长度 0-4
- type ∈ {{"mermaid", "table", "ascii"}}
- position ∈ {{"before", "after", "replace"}}
- 整段 content 用 \\n 转义换行,保证 JSON 合法
- 没有合适的可视化点时返回 {{"items": []}}
"""


def build_messages(
    *,
    chapter_title: str,
    chapter_body_md: str,
) -> list[dict[str, Any]]:
    """构造 LLM-3 messages 数组。"""
    return [
        {"role": "system", "content": LLM3_SYSTEM},
        {
            "role": "user",
            "content": LLM3_USER_TEMPLATE.format(
                chapter_title=chapter_title,
                chapter_body_md=chapter_body_md,
            ),
        },
    ]
