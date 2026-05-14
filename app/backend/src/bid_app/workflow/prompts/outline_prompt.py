"""LLM-1 提纲生成提示词(移植 v10 §4.3 + PR-M8-2 follow-up 层级目录)。

LLM-1 角色:综合 3 份输入文档(技术需求 / 打分规则 / 方案模板),输出一份
**层级目录**(章 → 节,叶子节点是可生成的最小章节单位)。下游 ``parse_outline``
把树展平到 ``state.chapters`` 给 Loop 用,每个叶子带 ``section`` 编号
("1.1" / "2.3.1")。

模型:``settings.llm1_outline_model``(默认 dashscope/deepseek-v4-flash)。
温度:0.3(目录要严谨稳定);Response Format:JSON Object;Max Tokens 6144。
"""
from __future__ import annotations

from typing import Any

LLM1_SYSTEM = """你是一位资深技术方案架构师,深耕投标方案撰写 10 年以上。\
你的任务是综合分析用户提供的技术需求文档、打分规则与方案模板,\
输出一份**层级化的章节目录**,作为后续逐节撰写正文的骨架。

你必须严格遵守以下规则:
1. 目录设计必须紧扣打分规则的各项权重——权重高的内容用更多节或更深篇幅
2. 目录结构应参照方案模板的层级,但允许根据需求实际调整
3. 输出**层级化**结构,最多 4 级:一级 = 章(分组),二级/三级/四级 = 节
   (越深越细)。**只有叶子节点**(没有 ``children`` 的)会被 LLM-2 单独写正文。
4. 一级章节数控制在 5-8 个;总叶子数(可生成节)控制在 12-30 个之间。
   超过 3 级的细分要克制,只在内容深度确实需要时才用。
5. 每个**叶子节**必须含明确的关键点列表,便于后续撰写时聚焦;非叶子
   (有 children 的)只给 title,不需要 key_points / target_pages。
6. 输出严格的 JSON 格式,不要任何前后缀文字、不要 markdown 代码块包裹

⭐ 占位符规则(D3):文中形如 `__ORG_xxxxxx__` `__PROJ_xxxxxx__` `__PERSON_xxxxxx__`
`__PHONE_xxxxxx__` `__EMAIL_xxxxxx__` `__IDCARD_xxxxxx__` 的标记是占位符,代表
被脱敏的敏感信息。请保留原占位符原样,不必复述,也不要替换为具体公司名 / 项目号 /
人名。下游会在导出时由人工核对。
"""


LLM1_USER_TEMPLATE = """请基于以下三份资料,为本次投标设计技术方案的**层级目录**。

## 一、技术需求文档
{tech_spec_excerpt}

## 二、打分规则(请特别关注各项权重)
{scoring_excerpt}

## 三、方案模板(参照其结构层级)
{template_excerpt}

{revision_section}

---

请输出 JSON 格式目录,严格遵循以下 schema(**最多 4 级**,只有叶子节点带
key_points / target_pages):

{{
  "toc": [
    {{
      "title": "项目背景与理解",
      "children": [
        {{
          "title": "招标方现状概览",
          "children": [
            {{
              "title": "公司规模与组织架构",
              "summary": "本节核心要点摘要(80 字以内)",
              "key_points": ["要点 1", "要点 2", "要点 3"],
              "target_pages": 2,
              "matched_scoring_items": ["对应的打分项名称"]
            }}
          ]
        }},
        {{
          "title": "项目需求理解",
          "summary": "...",
          "key_points": ["..."],
          "target_pages": 3,
          "matched_scoring_items": ["..."]
        }}
      ]
    }},
    {{
      "title": "技术方案",
      "children": [
        {{
          "title": "总体架构",
          "summary": "...",
          "key_points": ["..."],
          "target_pages": 3,
          "matched_scoring_items": ["..."]
        }}
      ]
    }}
  ]
}}

要求:
- 一级 5-8 个,叶子总数 12-30 个为宜
- 层级最多 4 级,**只在叶子上**给 ``summary`` / ``key_points`` / ``target_pages``
  / ``matched_scoring_items``;有 ``children`` 的节点只给 ``title``
- target_pages 根据打分权重和内容深度分配 1-6 页
- key_points 每个叶子 3-7 个
- matched_scoring_items 列出本节主要覆盖的打分项

请只输出 JSON 字符串,不要任何其他文字。
"""


REVISION_TEMPLATE = """---

## 上一轮目录的用户反馈

上一版目录用户审阅后提出以下修改意见,**必须**据此**整体重新设计**目录
(不要只挪动一两条,要做实质性调整;若用户指明某些节点,也保留其余合理部分):

{revision_feedback}
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
    revision_feedback: str = "",
) -> list[dict[str, Any]]:
    """构造 LLM-1 messages 数组。

    ``revision_feedback`` 非空时表示用户在目录确认页点了"请模型修改",
    把意见注入 prompt,LLM-1 据此重出目录(状态由 outline_review →
    generate_outline 的 conditional edge 触发)。
    """
    fb = revision_feedback.strip()
    revision_section = (
        REVISION_TEMPLATE.format(revision_feedback=fb) if fb else ""
    )
    return [
        {"role": "system", "content": LLM1_SYSTEM},
        {
            "role": "user",
            "content": LLM1_USER_TEMPLATE.format(
                tech_spec_excerpt=_excerpt(tech_spec_md, 8000),
                scoring_excerpt=_excerpt(scoring_md, 4000),
                template_excerpt=_excerpt(template_md, 4000),
                revision_section=revision_section,
            ),
        },
    ]
