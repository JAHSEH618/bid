"""LLM-3 提示词 — 章节可视化建议生成(移植 v10 §4.5.3 + D-EH 锚点驱动)。

提供 3 个 builder:

- ``build_messages``(向后兼容):自由发现 0–4 处可视化建议(原始 v10 行为),
  现仅作为 ``module`` / ``architecture`` 之外章节的兜底入口。
- ``build_sequence_messages``(D-EH):给定一个**流程名**+ 章节正文,输出
  单张 Mermaid ``sequenceDiagram`` 建议;由 ``gen_visuals`` 扫描
  ``对应时序图:<flow>`` 锚点后批量并发调用。
- ``build_architecture_messages``(D-EH):给定一组**层名**+ 章节正文,
  输出单张 Mermaid ``flowchart TD`` 七层架构图。

模型:``settings.llm3_visuals_model``(默认 dashscope/qwen3.6-flash)。
温度:0.4(略低保证 JSON 稳定);Response Format:JSON Object;Max Tokens 4096
(锚点驱动的单图调用降到 1024,见 ``gen_visuals``)。
"""

from __future__ import annotations

from typing import Any

LLM3_SYSTEM = """你是一位技术文档可视化设计师,擅长识别长文本中适合用图表、流程图、表格增强表达的位置。

你的任务:阅读用户提供的一段章节正文(Markdown 格式),输出一份 JSON 格式的可视化建议清单,\
标明每处建议在原文哪个位置插入、用什么类型、内容是什么。

可选可视化类型(type 字段):
- mermaid:流程图、时序图、甘特图、架构图(用 Mermaid 语法)
- table:Markdown 表格(对比、参数、矩阵)

输出规则:
1. 严格 JSON,不要任何前后缀文字、不要 markdown 代码块包裹
2. 每章建议 0-4 处可视化,质量优先于数量,无合适处则返回空数组
3. anchor 字段填一个能在原文中唯一定位的关键短语(8 字以内),不要填整段
4. position 取值:before(锚点前) / after(锚点后) / replace(替换锚点段落)
5. mermaid 的 content 必须是合法语法,直接可渲染
6. 不要建议给小标题、引言、总结段配图,只在确实能增强信息密度的位置建议
7. mermaid content 只写图表语法,不要包含 ```mermaid 围栏或 Markdown 说明
8. mermaid 不要写 style/classDef/class 等自定义配色,前端统一白底黑字渲染
9. 禁止输出 ASCII 框线图 / 文本框图;流程、架构、层级、模块关系一律用 mermaid
10. ⭐ 占位符规则(D3):正文中 `__XXX_xxxxxx__` 是脱敏占位符。anchor 与
    mermaid label 内若引用到原文敏感词,直接照抄占位符,不必复述也不要还原。
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
      "content": "Mermaid 语法或 Markdown 表格"
    }}
  ]
}}

约束:
- items 数组长度 0-4
- type ∈ {{"mermaid", "table"}}
- position ∈ {{"before", "after", "replace"}}
- 整段 content 用 \\n 转义换行,保证 JSON 合法
- 禁止使用 ASCII 框线图;凡是流程、架构、层级、模块关系都必须输出 mermaid
- 没有合适的可视化点时返回 {{"items": []}}
"""


def build_messages(
    *,
    chapter_title: str,
    chapter_body_md: str,
) -> list[dict[str, Any]]:
    """构造 LLM-3 messages 数组(向后兼容的自由发现入口)。"""
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


# ============== D-EH 锚点驱动:单图建议生成 ==============

_SEQ_SYSTEM = """你是一位技术时序图设计师。给定一段章节正文与本流程名,\
输出**且仅输出一张** Mermaid ``sequenceDiagram``,描述该流程在系统内的\
关键参与方与消息流转。

输出规则:
1. 严格 JSON,形如 ``{"items": [{"title": "...", "type": "mermaid",
   "anchor": "对应时序图:<flow>", "position": "after",
   "content": "sequenceDiagram\\n  ..." }]}``
2. ``items`` 数组**恰好 1 个元素**(不允许 0,不允许 2+)
3. ``anchor`` 必须**完整复制**用户给的锚点字符串,**包括** ``对应时序图:`` 前缀
4. ``position`` 固定为 ``after``
5. ``content`` 是单张 sequenceDiagram 的合法语法,不带 ```mermaid 围栏
6. 参与方 5-8 个为宜;中文标签用 ``["中文"]`` 双引号包,长度 ≤ 8 字
7. 不要 ``style`` / ``classDef`` / ``class`` 等装饰
8. 占位符 ``__XXX_xxxxxx__`` 照抄
"""


def build_sequence_messages(
    *,
    flow_name: str,
    chapter_title: str,
    chapter_body_md: str,
) -> list[dict[str, Any]]:
    """D-EH:给定单个流程名 + 章节正文,生成一张 sequenceDiagram 的 JSON 建议。"""
    user = (
        f"章节标题:{chapter_title}\n\n"
        f"目标流程名(锚点):**对应时序图:{flow_name}**\n\n"
        f"章节正文(供你理解该流程的上下文):\n"
        f"================================\n"
        f"{chapter_body_md}\n"
        f"================================\n\n"
        f"请输出 JSON,内含且仅含 1 张 ``sequenceDiagram`` 建议,\n"
        f"``anchor`` 字段必须**完整复制**为 ``对应时序图:{flow_name}``。"
    )
    return [
        {"role": "system", "content": _SEQ_SYSTEM},
        {"role": "user", "content": user},
    ]


_ARCH_SYSTEM = """你是一位系统架构图设计师。给定一组分层名与章节正文,\
输出**且仅输出一张** Mermaid ``flowchart TD``,自顶向下展示这些层之间\
的从属或调用关系。

输出规则:
1. 严格 JSON,形如 ``{"items": [{"title": "总体架构", "type": "mermaid",
   "anchor": "对应架构图:总体架构", "position": "after",
   "content": "flowchart TD\\n  ..." }]}``
2. ``items`` 数组**恰好 1 个元素**
3. ``anchor`` 必须固定为 ``对应架构图:总体架构``,``position`` 固定 ``after``
4. ``content`` 是单张 flowchart TD 的合法语法,**每一层各成一个节点**,
   顺序与给定层名一致,自顶向下用 ``-->`` 连接
5. 节点 label 用中文 + 双引号:``L1["接入层"]``
6. 不要 ``style`` / ``classDef`` / ``class``
"""


def build_architecture_messages(
    *,
    layers: list[str],
    chapter_title: str,
    chapter_body_md: str,
) -> list[dict[str, Any]]:
    """D-EH:给定一组层名,生成一张 flowchart TD 七层(或 N 层)架构图。"""
    layer_list = "、".join(layers)
    user = (
        f"章节标题:{chapter_title}\n\n"
        f"系统分层(自顶向下,固定顺序):{layer_list}\n\n"
        f"章节正文(供你理解层间关系的上下文):\n"
        f"================================\n"
        f"{chapter_body_md}\n"
        f"================================\n\n"
        f"请输出 JSON,内含且仅含 1 张 ``flowchart TD``,\n"
        f"``anchor`` 固定为 ``对应架构图:总体架构``。"
    )
    return [
        {"role": "system", "content": _ARCH_SYSTEM},
        {"role": "user", "content": user},
    ]
