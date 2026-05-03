"""LLM-2 章节正文生成提示词(移植 v10 §4.5.2 + §10.3)。

⭐ v10 关键变化:用户提示词内含 ``revision_feedback`` 反馈段,人工审核
"不通过"时把审核者写的修改建议拼进 prompt;模型据此重写本章。

模型:``settings.llm2_chapter_model``(默认 dashscope/qwen3.6-max-preview)。
温度:0.6(平衡创造性与一致性);Max Tokens 32768(单章 5 页留余量)。
"""
from __future__ import annotations

from typing import Any

LLM2_SYSTEM = """你是技术方案撰写专家,擅长将章节大纲扩展为深入、专业、可读性强的 Markdown 正文。

撰写规则:
1. 严格使用 Markdown 格式输出,不要使用一级标题(# 标题),从二级标题(## 标题)开始
2. 字数控制: 每页约 800 字,严格在「目标字数」到「目标字数 × 1.3」之间,不允许少于下限,也不允许超出 1.3 倍上限
3. 风格: 专业、严谨、条理清晰; 适当使用项目符号、有序列表、表格增强可读性
4. 不要写"以下是..."、"本章将..."这类元描述,直接进入正文
5. 章节末尾不要写"以上即为本章内容"等总结句
6. 技术细节要准确,涉及具体数字时给出来源或合理范围
7. 关键概念首次出现时简要解释,避免读者困惑

⚠️ 排版规范(必须遵守,渲染器才能正确分段):
- **段落之间必须有一个空行**(即用 `\\n\\n` 分隔),不允许两段紧挨
- **标题前后**(## / ### / ####)必须有空行
- **列表(- / 1.)与上下段落之间**必须有空行
- **代码块、表格** 与段落之间必须有空行
- 不要无意义连续 3 个以上空行;1 个空行(\\n\\n)足以分段

正确示例(注意每段之间空行):

```
## 系统架构

本系统采用前后端分离架构,前端使用 React,后端使用 FastAPI。

后端通过 PostgreSQL 持久化业务数据,Redis 处理消息队列。

### 核心模块

- 模块 A:负责认证授权
- 模块 B:负责数据采集

模块 A 与模块 B 通过 gRPC 通信。
```

错误示例(段落紧挨,**不要这样写**):

```
本系统采用前后端分离架构。
后端通过 PostgreSQL 持久化业务数据。
### 核心模块
- 模块 A:负责认证授权
模块 A 与模块 B 通过 gRPC 通信。
```

如果用户提示词中包含上一轮的修改建议,你必须严格按建议重写,\
不要做形式上的调整,要做实质性的重新组织和补充。
"""


LLM2_USER_TEMPLATE = """请撰写以下章节的完整 Markdown 正文。

## 章节信息
- **章节标题**: {title}
- **章节 ID**: {chapter_id}
- **要点摘要**: {summary}
- **目标页数**: {target_pages} 页
- **目标字数**: {target_chars} 字以上

### 必须覆盖的关键点

{key_points_block}

### 对应的打分项

{scoring_items_block}

## 上下文(技术需求摘要)
{tech_spec_excerpt}

## 上下文(打分规则摘要)
{scoring_excerpt}

{revision_section}

请直接输出本章 Markdown 正文,不要前后缀说明。
"""


REVISION_TEMPLATE = """====================
⚠️ 本章上一轮人工审核未通过(第 {retry_count} 轮)。

【上一轮生成的正文】

{previous_text}

【用户的修改意见】

{revision_feedback}

请基于上一轮正文 **结合** 用户修改意见做有针对性的修订:
- 如果意见是局部调整(如"第 X 段加 Y" / "表格改流程图"),保持其他部分基本一致
- 如果意见涉及整体结构问题(如"内容空泛" / "逻辑混乱"),做实质性重组
- 不要简单换措辞或表面微调
- 输出**完整修订后的本章 Markdown 正文**,而不是 diff
====================
"""

# ⚠️ retry_count > 0 但 previous_text 缺失(DB 查不到 abandoned 版本,理论
# 不该发生)时退化的旧模板,只带 feedback,不带 previous_text。
_REVISION_TEMPLATE_NO_PREVIOUS = """====================
⚠️ 本章上一轮人工审核未通过(第 {retry_count} 轮),修改建议如下:

{revision_feedback}

请严格依据以上建议重写本章,不要简单微调或换措辞,\
需要做实质性的重新组织、补充内容、修正问题。
====================
"""


def _excerpt(md: str, max_chars: int) -> str:
    if not md:
        return "(无)"
    if len(md) <= max_chars:
        return md
    return md[:max_chars] + "\n\n...(已截断)"


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "(无)"
    return "\n".join(f"- {it}" for it in items)


def build_messages(
    *,
    chapter: dict[str, Any],
    tech_spec_md: str,
    scoring_md: str,
    revision_feedback: str = "",
    retry_count: int = 0,
    previous_text: str = "",
) -> list[dict[str, Any]]:
    """构造 LLM-2 messages 数组。

    ⭐ R-18:revise 模式同时给 LLM 上一轮正文 + 用户意见 → patch 式修订。
    - retry_count == 0(原稿)/ revision_feedback 空 → 不带 revision_section
    - retry_count > 0 + previous_text + revision_feedback → 完整 REVISION_TEMPLATE
    - retry_count > 0 + 仅 revision_feedback(previous_text 缺,理论不该发生)
      → 退化到 _REVISION_TEMPLATE_NO_PREVIOUS,只带 feedback 兜底

    previous_text 不截断:qwen3.6-max-preview 上下文 256K,5-10K 字 partial
    完全装得下,patch 式 LLM-2 才能精准对齐用户意见。
    """
    target_pages = int(chapter.get("target_pages", 3) or 3)
    if not revision_feedback or retry_count <= 0:
        revision_section = ""
    elif previous_text:
        revision_section = REVISION_TEMPLATE.format(
            retry_count=retry_count,
            previous_text=previous_text,
            revision_feedback=revision_feedback.strip(),
        )
    else:
        # 兜底:理论不该发生(retry 必有上轮 abandoned 版本),保留旧行为
        revision_section = _REVISION_TEMPLATE_NO_PREVIOUS.format(
            retry_count=retry_count,
            revision_feedback=revision_feedback.strip(),
        )
    return [
        {"role": "system", "content": LLM2_SYSTEM},
        {
            "role": "user",
            "content": LLM2_USER_TEMPLATE.format(
                title=chapter.get("title", "(未命名章节)"),
                chapter_id=chapter.get("id", "ch_unknown"),
                summary=chapter.get("summary", ""),
                target_pages=target_pages,
                target_chars=target_pages * 800,
                key_points_block=_bullet_list(chapter.get("key_points") or []),
                scoring_items_block=_bullet_list(
                    chapter.get("matched_scoring_items") or []
                ),
                tech_spec_excerpt=_excerpt(tech_spec_md, 4000),
                scoring_excerpt=_excerpt(scoring_md, 2000),
                revision_section=revision_section,
            ),
        },
    ]
