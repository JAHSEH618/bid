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

- 章节主标题用 **`## {section} <章节标题>`**(二级标题,例如 `## 1.1 招标方现状概览`)
- 子节用 **`### {section}.N <子节标题>`**(三级标题,例如 `### 1.1.1 现状摘要`)
- 更细分用 **`#### {section}.N.M <小节标题>`**(四级标题,可选)
- **不要**用一级标题 `#`(渲染器会与全文标题冲突)
- **不要**多个 `##` 平级排列(章主用 `##`,子节必须降到 `###`)
- **不要**自创章节编号(如 `## 第一章`);严格使用我给的 ``section`` 字段值开头

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
## 3.1 服务质量控制与考核改进体系

本章围绕 **质量管控全闭环** 设计三层质检机制,确保 FCR 与 CSAT 指标稳定达标。

### 3.1.1 三层质检架构

质检体系按响应时效与覆盖深度分为三层:

| 层级 | 频次 | 覆盖率 | 核心指标 |
|:---:|:---:|:---:|:---|
| 实时质检 | 每通话 | **100%** | 关键违禁词、情绪异常 |
| 周抽检 | 每周一次 | 5% | 业务准确性、流程规范 |
| 月专项 | 每月一次 | 1% | 申诉案例、典型客诉 |

每层质检结果实时回传至 **质检看板**,主管可在 **15 分钟** 内介入异常坐席。

### 3.1.2 整改闭环机制

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

## 八、占位符规则(D3)

文中形如 `__ORG_xxxxxx__` `__PROJ_xxxxxx__` `__PERSON_xxxxxx__` `__PHONE_xxxxxx__`
`__EMAIL_xxxxxx__` `__IDCARD_xxxxxx__` 的标记是占位符,代表被脱敏的敏感信息。
请保留原占位符原样,不必复述,也不要替换为具体公司名 / 项目号 / 人名 / 电话。
下游会在导出时由人工核对。
"""


LLM2_USER_TEMPLATE = """请撰写以下章节的完整 Markdown 正文。

## 章节信息
- **章节编号(section)**: {section}  ← 标题首行必须用 ``## {section} {title}``
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


# Phase 2A (2026-05-16):用 BM25 替换 Phase 1B 的「章标题关键词 → 桶」静态
# 映射。Phase 1B 的硬编码关键字表逻辑保留为 BM25 不可用 / 实体为空时的
# 默认上下文桶,理论上不会被走到(因为这条分支也意味着我们走 markdown 截断
# 回退路径,不会用 buckets)。Phase 2B 接 tool calling 时同一份 BlackboardIndex
# 复用,LLM 自己发起查询。
_CHAPTER_FALLBACK_BUCKETS = ("scoring_rules", "technical_requirements")


def _build_chapter_query(chapter: dict[str, Any]) -> str:
    """从 chapter 元数据拼一段查询文本,喂给 BM25。

    包含 title / parent_titles / key_points / matched_scoring_items。
    """
    parts: list[str] = [str(chapter.get("title") or "")]
    parents = chapter.get("parent_titles") or []
    if isinstance(parents, list):
        parts.extend(str(p) for p in parents)
    matched = chapter.get("matched_scoring_items") or []
    if isinstance(matched, list):
        parts.extend(str(m) for m in matched)
    key_points = chapter.get("key_points") or []
    if isinstance(key_points, list):
        parts.extend(str(p) for p in key_points)
    summary = chapter.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())
    return " ".join(p for p in parts if p)


def _render_entries_grouped(
    entries: list[dict[str, Any]],
    *,
    char_limit: int = 8000,
) -> str:
    """把 BM25 选出的 entries 按 bucket 分组渲染,给 LLM-2 prompt 用。

    保留 BM25 分数顺序(同 bucket 内按分数降序),每个 bucket 一段 ###。
    超过 char_limit 截断尾部 + 提示。
    """
    if not entries:
        return ""
    # 按 bucket 分组,保持桶在 ENTITY_BUCKETS 里的顺序(更稳定的读感)
    from .categorize_blackboard import _BUCKET_LABELS_ZH
    from .categorize_blackboard import ENTITY_BUCKETS as _ALL_BUCKETS

    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        b = entry.get("bucket") or "project_info"
        grouped.setdefault(b, []).append(entry)

    sections: list[str] = []
    used = 0
    for bucket in _ALL_BUCKETS:
        items = grouped.get(bucket) or []
        if not items:
            continue
        label = _BUCKET_LABELS_ZH.get(bucket, bucket)
        lines: list[str] = [f"### {label} ({bucket})"]
        for i, entry in enumerate(items, 1):
            content = entry.get("content") or ""
            if not isinstance(content, str):
                continue
            meta_bits: list[str] = []
            source = entry.get("source_doc")
            section = entry.get("section")
            if isinstance(source, str) and source:
                meta_bits.append(source)
            if isinstance(section, str) and section:
                meta_bits.append(section)
            meta = f" *({' · '.join(meta_bits)})*" if meta_bits else ""
            line = f"{i}. {content}{meta}"
            if used + len(line) > char_limit:
                lines.append("...(已截断,完整内容查实体黑板)")
                sections.append("\n".join(lines))
                return "\n\n".join(sections)
            lines.append(line)
            used += len(line) + 1
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _pick_buckets_for_chapter(chapter: dict[str, Any]) -> list[str]:
    """Phase 1B 兼容入口:保留给老测试用。BM25 路径不再走这里。
    返回 ``_CHAPTER_FALLBACK_BUCKETS`` 之外永远不再扩,告诉调用方默认两桶。
    """
    return list(_CHAPTER_FALLBACK_BUCKETS)


def build_messages(
    *,
    chapter: dict[str, Any],
    tech_spec_md: str,
    scoring_md: str,
    revision_feedback: str = "",
    retry_count: int = 0,
    previous_text: str = "",
    blackboard_entities: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """构造 LLM-2 messages 数组。

    ⭐ R-18:revise 模式同时给 LLM 上一轮正文 + 用户意见 → patch 式修订。
    - retry_count == 0(原稿)/ revision_feedback 空 → 不带 revision_section
    - retry_count > 0 + previous_text + revision_feedback → 完整 REVISION_TEMPLATE
    - retry_count > 0 + 仅 revision_feedback(previous_text 缺,理论不该发生)
      → 退化到 _REVISION_TEMPLATE_NO_PREVIOUS,只带 feedback 兜底

    previous_text 不截断:qwen3.6-max-preview 上下文 256K,5-10K 字 partial
    完全装得下,patch 式 LLM-2 才能精准对齐用户意见。

    ⭐ Phase 2A (2026-05-16):有 ``blackboard_entities`` 时走 BM25 检索:
    用 chapter title / parent_titles / matched_scoring_items / key_points
    拼查询,从 10 桶里挑出 top-K 个最相关 entry(跨桶),按 bucket 分组渲染。
    旧 Phase 1B 的「章标题关键词命中桶」静态规则被替代——BM25 召回率更高,
    不会因为标题没命中关键字就漏掉真正相关的条款。
    无 entities 时回退 markdown 截断。
    """
    from .categorize_blackboard import has_any_entries

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

    if has_any_entries(blackboard_entities):
        # BM25 路径:章节查询 → 跨桶检索 top_k
        from ...services.blackboard_retrieval import BlackboardIndex

        index = BlackboardIndex(blackboard_entities)
        query = _build_chapter_query(chapter)
        # top_k=12 在大多数项目里能给到 LLM-2 足够信息又不过长;实测每条 entry
        # 50-300 字符,12 条 ~2-4k 字符,远低于 prompt 长度上限
        hits = index.search(entity_types=None, query=query, top_k=12)
        # BM25 严格按 token 重叠召回,章节标题没明确命中黑板原文时可能为空。
        # 补一份「评分规则 + 技术要求」的前几条作为通用上下文(每个投标章节
        # 都该看到这两类核心信息),与 hits 去重后合并。
        baseline = index.search(
            entity_types=list(_CHAPTER_FALLBACK_BUCKETS),
            query="",
            top_k=4,
        )
        seen_contents = {h["content"] for h in hits}
        for b in baseline:
            if b["content"] in seen_contents:
                continue
            hits.append(b)
            seen_contents.add(b["content"])
        if hits:
            bb_section = _render_entries_grouped(hits, char_limit=8000)
            context_block = (
                f"## 上下文(BM25 从实体黑板按本章主题挑出的 {len(hits)} 条 + "
                f"评分/技术基线条目)\n\n{bb_section}"
            )
        else:
            # entities 有内容但 BM25 + baseline 全返空(理论不该发生),
            # 退到 markdown 截断兜底
            context_block = (
                f"## 上下文(技术需求摘要)\n{_excerpt(tech_spec_md, 4000)}\n\n"
                f"## 上下文(打分规则摘要)\n{_excerpt(scoring_md, 2000)}"
            )
    else:
        # 回退:实体黑板未生成 / 为空 → 老 markdown 截断输入
        context_block = (
            f"## 上下文(技术需求摘要)\n{_excerpt(tech_spec_md, 4000)}\n\n"
            f"## 上下文(打分规则摘要)\n{_excerpt(scoring_md, 2000)}"
        )

    user_content = (
        f"请撰写以下章节的完整 Markdown 正文。\n\n"
        f"## 章节信息\n"
        f"- **章节编号(section)**: {chapter.get('section') or '1'}  ← 标题首行必须用 ``## {chapter.get('section') or '1'} {chapter.get('title', '(未命名章节)')}``\n"
        f"- **章节标题**: {chapter.get('title', '(未命名章节)')}\n"
        f"- **章节 ID**: {chapter.get('id', 'ch_unknown')}\n"
        f"- **要点摘要**: {chapter.get('summary', '')}\n"
        f"- **目标页数**: {target_pages} 页\n"
        f"- **目标字数**: {target_pages * 800} 字以上\n\n"
        f"### 必须覆盖的关键点\n\n"
        f"{_bullet_list(chapter.get('key_points') or [])}\n\n"
        f"### 对应的打分项\n\n"
        f"{_bullet_list(chapter.get('matched_scoring_items') or [])}\n\n"
        f"{context_block}\n\n"
        f"{revision_section}\n\n"
        f"请直接输出本章 Markdown 正文,不要前后缀说明。\n"
    )

    return [
        {"role": "system", "content": LLM2_SYSTEM},
        {"role": "user", "content": user_content},
    ]
