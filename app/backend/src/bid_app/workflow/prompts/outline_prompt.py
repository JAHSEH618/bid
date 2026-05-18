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
3. **输出 4 级层级结构**:一级 = 章(分组)、二级 = 节、三级 = 小节、
   四级 = 条目。**强制**至少在主要章节(权重大、内容厚)下展开到三 / 四级,
   不要让整本只停在二级浅层。**只有叶子节点**(没有 ``children``)被 LLM-2
   单独写正文。
4. 一级章节数 5-8 个;二级 / 三级 / 四级合计的叶子数控制在 25-50 个之间。
   每个一级章下挂 3-6 个二级节,大多数二级节再展开 2-4 个三级小节,
   关键深度内容再展开 2-3 个四级条目。
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
- 一级 5-8 个,叶子总数 25-50 个为宜
- **优先把权重大的一级章节展开到 3 级 / 4 级**,体现深度
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


def _render_skeleton_block(skeleton: list[dict[str, Any]]) -> str:
    """把骨架 JSON 转成 LLM-1 可读的指令块。

    ``fixed`` 节点必须原样出现在输出 toc 中,标题、顺序不可改;
    ``expandable`` 节点要求 LLM 在该位置展开 ``expand_min..expand_max`` 个
    叶子,继承 ``child_chapter_type``;其它节点(``fixed=False`` 默认)允许
    LLM 在保持上下文连贯的前提下自由调整。
    """
    lines: list[str] = []

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        indent = "  " * depth
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = node.get("title", "")
            ct = node.get("chapter_type")
            slot = node.get("template_slot")
            fixed = node.get("fixed")
            expandable = node.get("expandable")
            tags: list[str] = []
            if fixed:
                tags.append("fixed")
            if expandable:
                emin = node.get("expand_min", 1)
                emax = node.get("expand_max", 8)
                cct = node.get("child_chapter_type", "normal")
                tags.append(f"expandable[{emin}-{emax}, child_chapter_type={cct}]")
            if ct:
                tags.append(f"chapter_type={ct}")
            if slot:
                tags.append(f"template_slot={slot}")
            req = node.get("required_anchors") or []
            if req:
                tags.append(f"required_anchors={req}")
            tag_str = f"  ({'; '.join(tags)})" if tags else ""
            lines.append(f"{indent}- {title}{tag_str}")
            children = node.get("children") or []
            if isinstance(children, list) and children:
                walk(children, depth + 1)

    walk(skeleton, 0)
    return "\n".join(lines)


_SKELETON_INSTRUCTION = """## 模版骨架(必须遵循,D-EF)

下面是本类项目的**标准应答骨架**。你输出的 toc **必须**满足:

1. 所有 ``(fixed)`` 节点的标题与顺序原样保留,不可删除、不可改名、不可
   并入其它节点。
2. 所有 ``(expandable[min-max, ...])`` 节点不直接生成正文,而是在该
   位置展开 ``expand_min`` ~ ``expand_max`` 个**叶子节点**,每个叶子继承
   ``child_chapter_type``;展开的叶子标题须根据招标材料与评分要点拟定,
   覆盖核心业务能力。
3. 叶子节点上,**必须**把骨架给的 ``chapter_type / template_slot /
   required_anchors`` **原样写到 JSON 字段**(下游会据此分流生成器与
   校验器,缺失会被自动拒收)。
4. 仅 ``chapter_type=normal`` 的叶子允许自由设计 ``key_points``;
   ``image_only`` / ``table_only`` 的叶子由模板填充,只需保留标题
   与 ``chapter_type``,``key_points`` 可填空数组。

骨架定义:

```
{skeleton_block}
```
"""


def build_messages(
    *,
    tech_spec_md: str,
    scoring_md: str,
    template_md: str,
    revision_feedback: str = "",
    blackboard_entities: dict[str, Any] | None = None,
    tool_calling_enabled: bool = False,
    skeleton: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """构造 LLM-1 messages 数组。

    ``revision_feedback`` 非空时表示用户在目录确认页点了"请模型修改",
    把意见注入 prompt,LLM-1 据此重出目录(状态由 outline_review →
    generate_outline 的 conditional edge 触发)。

    ⭐ Phase 1B (2026-05-16):``blackboard_entities`` 是 categorize_blackboard
    节点产出的 10 桶 JSON。给到时优先用它(结构化 + 分类好的关键条款),
    比原 ``tech_spec_md[:8000]`` 截断信息密度高。Phase 1A 通常会有产出;
    为空 / None(LLM-0 失败 / 老项目) → 降级回 markdown 截断。

    ⭐ Phase 2B (2026-05-16):``tool_calling_enabled=True`` 时**不**把 10
    桶 dump 进 user prompt(避免上下文撑爆 + 与 tool 调用结果重复),
    改为告知 LLM「黑板已分桶,需要时调 search_blackboard 取」。entities
    本身仍传到 prompt 头部给一份 ``bucket_counts`` 概览,让 LLM 知道有
    什么可问。要 entities 为空时退到 markdown 截断,不开 tool。

    ⭐ D-EF (2026-05-18):``skeleton`` 是模版骨架包的 ``skeleton`` 子结构,
    给到时作为强约束注入 prompt;LLM-1 在骨架基础上做裁剪 + 展开,而不是
    从零设计目录。``None`` 时退到旧自由模式(用于未识别项目类别 / 关闭开关)。
    """
    from .categorize_blackboard import (
        has_any_entries,
        render_buckets_for_prompt,
    )

    fb = revision_feedback.strip()
    revision_section = (
        REVISION_TEMPLATE.format(revision_feedback=fb) if fb else ""
    )

    skeleton_section = ""
    if skeleton:
        skeleton_section = _SKELETON_INSTRUCTION.format(
            skeleton_block=_render_skeleton_block(skeleton)
        )

    if tool_calling_enabled and has_any_entries(blackboard_entities):
        # Tool 路径:只给 bucket_counts 概览 + 引导 LLM 主动检索
        assert blackboard_entities is not None
        counts: list[str] = []
        for bucket in [
            "project_info", "company_info", "personnel_info",
            "scoring_rules", "technical_requirements",
            "qualification_requirements", "timeline_constraints",
            "commercial_terms", "compliance_constraints", "risk_signals",
        ]:
            n = len(blackboard_entities.get(bucket) or [])
            counts.append(f"- ``{bucket}``: {n} 条")
        bucket_summary = "\n".join(counts)
        user_content = (
            "请为本次投标设计技术方案的**层级目录**。\n\n"
            "## 招标材料实体黑板(已分桶,共 10 类)\n\n"
            f"{bucket_summary}\n\n"
            "你必须**至少调用 `search_blackboard` 工具 2-4 次**,先把"
            "**评分细则**、**技术要求**、**风险信号** 三类拉出来读完,"
            "再据此设计目录。需要 confirm 资质 / 人员要求时再调一次。\n\n"
            "调用建议:\n"
            "- 第 1 次:`entity_types=[\"scoring_rules\"]`,query 留空,top_k=10\n"
            "- 第 2 次:`entity_types=[\"technical_requirements\"]`,query 按你想覆盖的子主题写,top_k=8\n"
            "- 第 3 次:`entity_types=[\"risk_signals\", \"compliance_constraints\"]`,top_k=6\n"
            "- 视需要再问其它桶\n\n"
            f"{skeleton_section}\n\n"
            f"{revision_section}\n\n"
            + _OUTLINE_SCHEMA_TAIL
        )
    elif has_any_entries(blackboard_entities):
        # Phase 1B 静态注入:全 10 桶 dump
        material_section = render_buckets_for_prompt(
            blackboard_entities, per_bucket_char_limit=5000
        )
        user_content = (
            f"请基于以下结构化材料黑板,为本次投标设计技术方案的**层级目录**。\n\n"
            f"## 材料黑板(10 个实体桶)\n\n{material_section}\n\n"
            f"{skeleton_section}\n\n"
            f"{revision_section}\n\n"
            + _OUTLINE_SCHEMA_TAIL
        )
    else:
        # 回退:实体黑板没生成 / 为空 → 老 markdown 截断输入
        user_content = LLM1_USER_TEMPLATE.format(
            tech_spec_excerpt=_excerpt(tech_spec_md, 8000),
            scoring_excerpt=_excerpt(scoring_md, 4000),
            template_excerpt=_excerpt(template_md, 4000),
            revision_section=(skeleton_section + "\n\n" + revision_section).strip(),
        )
    return [
        {"role": "system", "content": LLM1_SYSTEM},
        {"role": "user", "content": user_content},
    ]


# 把原 user template 里「请输出 JSON 格式目录,严格遵循以下 schema...」
# 这段尾巴拆出来,Phase 1B 走结构化材料黑板路径时复用,避免重复维护
# 两套 schema 描述。
_OUTLINE_SCHEMA_TAIL = """---

请输出 JSON 格式目录,严格遵循以下 schema(**最多 4 级**,只有叶子节点带
key_points / target_pages):

{
  "toc": [
    {
      "title": "项目背景与理解",
      "children": [
        {
          "title": "招标方现状概览",
          "children": [
            {
              "title": "公司规模与组织架构",
              "summary": "本节核心要点摘要(80 字以内)",
              "key_points": ["要点 1", "要点 2", "要点 3"],
              "target_pages": 2,
              "matched_scoring_items": ["对应的打分项名称"],
              "chapter_type": "normal",
              "template_slot": "",
              "required_anchors": []
            }
          ]
        }
      ]
    }
  ]
}

要求:
- 一级 5-8 个,叶子总数 25-50 个为宜
- **优先把权重大的一级章节展开到 3 级 / 4 级**,体现深度
- 层级最多 4 级,**只在叶子上**给 ``summary`` / ``key_points`` / ``target_pages``
  / ``matched_scoring_items`` / ``chapter_type`` / ``template_slot`` /
  ``required_anchors``;有 ``children`` 的节点只给 ``title``
- target_pages 根据打分权重和内容深度分配 1-6 页
- key_points 每个叶子 3-7 个(``image_only`` / ``table_only`` 叶子可空数组)
- matched_scoring_items 列出本节主要覆盖的打分项
- **D-EF**:若提供了模版骨架,叶子上的 ``chapter_type`` / ``template_slot``
  / ``required_anchors`` 必须**原样照抄**骨架对应位置;未提供骨架时
  ``chapter_type`` 默认 ``"normal"``,``template_slot`` 与
  ``required_anchors`` 留空

请只输出 JSON 字符串,不要任何其他文字。
"""
