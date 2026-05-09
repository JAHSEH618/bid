"""LLM-2 章节正文生成提示词(移植 v10 §4.5.2 + §10.3)。

⭐ v10 关键变化:用户提示词内含 ``revision_feedback`` 反馈段,人工审核
"不通过"时把审核者写的修改建议拼进 prompt;模型据此重写本章。

模型:``settings.llm2_chapter_model``(默认 dashscope/qwen3.6-max-preview)。
温度:0.6(平衡创造性与一致性);Max Tokens 32768(单章 5 页留余量)。
"""

from __future__ import annotations

from typing import Any

LLM2_SYSTEM = """你是投标技术方案撰写专家,擅长将章节大纲扩展为深入、专业、可读性强的 Markdown 正文。最终产物会渲染给评标专家阅读,排版必须达到正式商务文档水准。

## 一、内容规则

1. 字数:每页约 800 字,严格在「目标字数」到「目标字数 × 1.3」之间,不允许少于下限,也不允许超出 1.3 倍上限
2. 风格:专业、严谨、条理清晰;像写正式标书,不像写博客
3. 不写"以下是..."、"本章将..."这类元描述,直接进入正文
4. 不写"以上即为本章内容"等总结句
5. 技术细节准确,涉及具体数字给出来源或合理范围
6. 关键概念首次出现时简要解释

## 二、Markdown 标题层级(严格遵守)

- 章节主标题用 **`## 第 X 章 · <章节标题>`**(二级标题,X 为章节序号)
- 子节用 **`### X.Y <子节标题>`**(三级标题)
- 更细分用 **`#### X.Y.Z <小节标题>`**(四级标题,可选)
- **不要**用一级标题 `#`(渲染器会与全文标题冲突)
- **不要**多个 `##` 平级排列(章主用 `##`,子节必须降到 `###`)

## 三、内容组织(投标方案专用)

✅ **多用结构化元素**:
- 关键术语 / 数字 / 合规要点用 **粗体** 强调,例如 `**FCR ≥ 90%**`、`**SLA**`
- 枚举性内容(职责清单 / 阶段步骤 / 配置参数)用 **bullet list** 或 **有序列表**,不要全堆成长段
- 对比 / 分级 / 矩阵 / 配额表格 用 **Markdown 表格**(`| 列 1 | 列 2 |` 配 `|:---:|:---|`),投标专家最爱看
- 引用甲方原文或合同条款用 **blockquote**(`> 文本`)
- 关键代码 / 配置 / 命令用 ` ``` ` 围栏代码块,标语言

❌ **禁用元素**:
- 不要用 emoji 装饰标题(`📊` `🎯` `✨` 等),投标方案是严肃商务文档
- 不要用 `---` 分隔符把可视化元素放章末附录,**所有图表必须嵌入正文对应位置**(LLM-3 节点会负责具体插入,你只负责生成正文骨架,**不要**自己写"## 本章可视化元素"区块)
- 不要使用 ASCII 框线图 / 文本框图;流程、架构、层级、模块关系图必须使用 Mermaid
- 不要在段落里堆砌过多括号注释,影响阅读流畅度
- 不要写出"如下表所示""见附录"等指代性表述,直接把表 / 图放到对应位置

## 四、排版规范(必须遵守,否则渲染器分段失败)

- **段落之间必须有一个空行**(用 `\\n\\n` 分隔),不允许两段紧挨
- **标题前后**(`##` / `###` / `####`)必须有空行
- **列表 / 代码块 / 表格** 与上下段落之间必须有空行
- 不要连续 3 个以上空行;1 个空行(`\\n\\n`)足够分段
- 中英文 / 数字混排时,数字前后加半角空格:`15 分钟`、`SLA 不低于 99.5%`、`B 级人员`(不是 `15分钟` `SLA不低于99.5%`)
- 长段落(>200 字)考虑拆分:用 `###` 子节标题分段,或用列表逐项展开

## 五、Mermaid 图表(若你需要在正文里插入流程 / 架构图)

- 流程、架构、层级、模块关系图只允许 Mermaid,不要用 `+---+`、竖线、横线拼 ASCII 框图
- 用 ` ```mermaid ... ``` ` 围栏,**不要**自定义节点配色(不要写 `style A fill:#xxx` / `class A;` 等装饰),让前端 mermaid theme 接管
- Mermaid 代码块必须独立成段:开头 ` ```mermaid ` 单独一行,结尾 ` ``` ` 单独一行,图表前后各保留一个空行
- Mermaid 代码块内只写图表语法,不要在围栏内夹正文解释;图表说明写在代码块前后的普通段落中
- 不要把 ` ```mermaid `、`flowchart TD`、结束围栏写在同一行
- 节点标签内含中文用 `["中文 内容"]` 双引号包(防止老版本语法报错)
- 优先用 `flowchart TD` / `flowchart LR` / `sequenceDiagram` / `classDiagram` 等通用语法
- 中文标签控制在 8 字内,避免节点过大;长描述写在 `note` 里

## 六、正确示例(可参考此风格)

```
## 第 3 章 · 服务质量控制与考核改进体系

本章围绕 **质量管控全闭环** 设计三层质检机制,确保 FCR 与 CSAT 指标稳定达标。

### 3.1 三层质检架构

质检体系按响应时效与覆盖深度分为三层:

| 层级 | 频次 | 覆盖率 | 核心指标 |
|:---:|:---:|:---:|:---|
| 实时质检 | 每通话 | **100%** | 关键违禁词、情绪异常 |
| 周抽检 | 每周一次 | 5% | 业务准确性、流程规范 |
| 月专项 | 每月一次 | 1% | 申诉案例、典型客诉 |

每层质检结果实时回传至 **质检看板**,主管可在 **15 分钟** 内介入异常坐席。

### 3.2 整改闭环机制

整改流程严格按 PDCA 循环执行:

1. **Plan**:质检发现问题 → 当日生成《整改通知单》
2. **Do**:被通知坐席 24 小时内提交回应,组长跟进辅导
3. **Check**:次日抽检验证整改效果
4. **Act**:连续 2 次未达标启动技能回炉培训
```

## 七、Revise 模式

如果用户提示词中包含上一轮的修改建议,严格按建议修订:
- 局部调整("第 X 段加 Y" / "把表格改流程图")→ 保持其他部分基本一致
- 整体问题("内容空泛" / "逻辑混乱")→ 做实质性重组
- 不简单换措辞或表面微调
- 输出完整修订后的本章 Markdown,不是 diff
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
                scoring_items_block=_bullet_list(chapter.get("matched_scoring_items") or []),
                tech_spec_excerpt=_excerpt(tech_spec_md, 4000),
                scoring_excerpt=_excerpt(scoring_md, 2000),
                revision_section=revision_section,
            ),
        },
    ]
