"""LLM-1 提纲生成提示词(移植 v10 §4.3)。

LLM-1 角色:综合 3 份输入文档(技术需求 / 打分规则 / 方案模板),输出一份
结构化的章节提纲 JSON。下游 ``parse_outline`` 节点解析后塞进 Loop 输入。

模型:``settings.llm1_outline_model``(默认 dashscope/deepseek-v4-flash)。
温度:0.3(提纲要严谨稳定);Response Format:JSON Object;Max Tokens 6144。
"""
from __future__ import annotations

from typing import Any

LLM1_SYSTEM = """你是一位资深技术方案架构师,深耕投标方案撰写 10 年以上。\
你的任务是综合分析用户提供的技术需求文档、打分规则与方案模板,\
输出一份结构化、覆盖度高、能精准对应打分项的章节提纲。

你必须严格遵守以下规则:
1. 章节设计必须紧扣打分规则的各项权重——权重高的内容用更多章节或更深篇幅
2. 章节结构应参照方案模板的层级,但允许根据需求实际调整
3. 输出严格的 JSON 格式,不要任何前后缀文字、不要 markdown 代码块包裹
4. 每章必须含明确的关键点列表,便于后续撰写时聚焦
5. 章节数量控制在 8-15 个之间
"""


LLM1_USER_TEMPLATE = """请基于以下三份资料,为本次投标设计技术方案章节提纲。

## 一、技术需求文档
{tech_spec_excerpt}

## 二、打分规则(请特别关注各项权重)
{scoring_excerpt}

## 三、方案模板(参照其结构层级)
{template_excerpt}

---

请输出 JSON 格式提纲,严格遵循以下 schema:

{{
  "chapters": [
    {{
      "id": "ch_01",
      "title": "章节标题(简洁有力)",
      "summary": "本章核心要点摘要(80 字以内)",
      "key_points": ["要点 1", "要点 2", "要点 3"],
      "target_pages": 3,
      "matched_scoring_items": ["对应的打分项名称"]
    }}
  ]
}}

要求:
- 8-15 个章节为宜
- target_pages 根据打分权重和内容深度分配 1-6 页
- key_points 每章 3-7 个
- matched_scoring_items 列出本章主要覆盖的打分项

请只输出 JSON 字符串,不要任何其他文字。
"""


def _excerpt(md: str, max_chars: int) -> str:
    """超长文档剪裁 — 保留前 ``max_chars`` 字符,提示词输入安全上限。"""
    if not md:
        return "(无)"
    if len(md) <= max_chars:
        return md
    return md[:max_chars] + "\n\n...(已截断,完整文档见原始上传)"


def build_messages(
    *,
    tech_spec_md: str,
    scoring_md: str,
    template_md: str,
) -> list[dict[str, Any]]:
    """构造 LLM-1 messages 数组。"""
    return [
        {"role": "system", "content": LLM1_SYSTEM},
        {
            "role": "user",
            "content": LLM1_USER_TEMPLATE.format(
                tech_spec_excerpt=_excerpt(tech_spec_md, 8000),
                scoring_excerpt=_excerpt(scoring_md, 4000),
                template_excerpt=_excerpt(template_md, 4000),
            ),
        },
    ]
