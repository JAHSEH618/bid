"""Categorize blackboard 提示词 (Phase 1A, 2026-05-16)。

把材料黑板拆分到 10 个固定实体桶,后续给 LLM-1 outline / LLM-2 chapter
做结构化检索源(Phase 2 接 BM25 + LiteLLM tool calling)。

时机:``material_understanding_review`` 用户 pass / skip 之后、
``generate_outline`` 之前(用户还在 revise 材料理解时不跑,节省 LLM 调用)。

模型:复用 ``settings.llm1_outline_model``。
"""

from __future__ import annotations

from typing import Any

# 10 个固定桶,与前端 / BM25 service / tool 定义保持一致
ENTITY_BUCKETS = [
    "project_info",              # 项目背景 / 招标方 / 招标项目本身的基本信息
    "company_info",              # 招标方组织 / 投标方资质相关的公司层面要求
    "personnel_info",            # 项目经理 / 关键岗位的资质 / 人数 / 经验要求
    "scoring_rules",             # 评分细则与权重(投标的核心导向)
    "technical_requirements",    # 技术参数 / SLA / 接口 / 性能 / 平台
    "qualification_requirements",# 投标资质 / 证书 / 类似项目业绩硬门槛
    "timeline_constraints",      # 工期 / 节点 / 截止时间 / 实施阶段划分
    "commercial_terms",          # 报价规则 / 付款条件 / 商务条款
    "compliance_constraints",    # 法律 / 法规 / 合同 / 必须项 / 强制条款
    "risk_signals",              # 容易踩雷 / 一票否决 / 隐含风险点
]


CATEGORIZE_SYSTEM = """你是投标方案分析师,专长是把招标材料黑板拆解到结构化实体桶,\
给后续生成模型(LLM-1 提纲、LLM-2 章节)做检索源。

任务:
读黑板内容,把每条有用信息归到下面 10 个实体桶里。**多桶归属是允许的**——
比如"项目经理须有 10 年以上类似项目经验"同时属于 ``personnel_info`` 和
``qualification_requirements``,放进 tags 数组里。

10 个固定实体桶(只能用这些,不要自创):

| bucket | 含义 |
|---|---|
| project_info | 项目背景 / 招标方 / 项目本身基本信息 |
| company_info | 招标方组织架构 / 投标方公司层面要求 |
| personnel_info | 项目经理 / 关键岗位的资质、人数、经验 |
| scoring_rules | 评分细则与权重(投标核心导向) |
| technical_requirements | 技术参数 / SLA / 接口 / 性能 / 平台 |
| qualification_requirements | 投标资质 / 证书 / 类似项目业绩硬门槛 |
| timeline_constraints | 工期 / 节点 / 截止时间 / 实施阶段 |
| commercial_terms | 报价规则 / 付款条件 / 商务条款 |
| compliance_constraints | 法律 / 法规 / 合同必须项 / 强制条款 |
| risk_signals | 容易踩雷 / 一票否决 / 隐含风险点 |

每条 entry 形如:
    {
      "tags": ["scoring_rules", "risk_signals"],   // 多桶归属用 list
      "content": "提供 7×24 小时驻场服务,响应时间不超过 15 分钟",
      "source_doc": "tech_spec.docx",               // 可选,来自哪份文档
      "section": "技术需求 §4.2"                    // 可选,文档内章节定位
    }

约束:
- **不要省略**信息;原文里出现的硬性条款 / 量化指标 / 资质门槛**必须**入桶
- **不要复述同一条**;同一句话归到 1–3 个桶就够,不要 10 个桶都塞
- **不要自创桶**;凡是不在上表 10 个名字内的归类一律拒绝
- **保留占位符** ``__ORG_xxxxxx__`` / ``__PROJ_xxxxxx__`` / ``__PERSON_xxxxxx__`` 等原样
- 内容尽量保留原文措辞,不要二次概括

输出严格 JSON,**仅 JSON**,不要前后缀文字、不要 markdown 代码块包裹:

{
  "project_info": [{"tags": ["project_info"], "content": "...", "source_doc": "...", "section": "..."}],
  "company_info": [...],
  ...10 个桶都给,即使空也给空数组 []
}

⭐ 占位符规则 (D3):文中的 ``__ORG_xxxxxx__`` / ``__PROJ_xxxxxx__`` 等是
脱敏占位符,保留原样,不必复述。
"""


CATEGORIZE_USER_TEMPLATE = """请阅读以下材料黑板,按上面 10 个桶把信息分类输出 JSON。

## 材料黑板 (清洗后的 HTML 片段,按 id ASC 拼接)

================================
{blackboard_excerpt}
================================

请按 system 指示输出 JSON,**10 个桶必须都出现**(空就是 [])。
"""


def build_messages(*, blackboard_excerpt: str) -> list[dict[str, Any]]:
    """构造 categorize_blackboard messages 数组。

    与 material_understanding 不同:此节点没有 revise 路径(用户在材料
    理解页 pass 后才跑,跑出错就抛 ``CategorizationFailed``)。
    """
    return [
        {"role": "system", "content": CATEGORIZE_SYSTEM},
        {
            "role": "user",
            "content": CATEGORIZE_USER_TEMPLATE.format(
                blackboard_excerpt=blackboard_excerpt or "(空)",
            ),
        },
    ]


def normalize_entities(parsed: Any) -> dict[str, list[dict[str, Any]]]:
    """把 LLM 输出 normalize 成稳定 schema:
    - 10 个桶必须存在(缺的补 [])
    - 每条 entry 至少有 ``content`` (str),其它字段可选
    - 自创的桶名直接丢弃
    """
    out: dict[str, list[dict[str, Any]]] = {b: [] for b in ENTITY_BUCKETS}
    if not isinstance(parsed, dict):
        return out
    for bucket in ENTITY_BUCKETS:
        items = parsed.get(bucket) or []
        if not isinstance(items, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            tags = item.get("tags") or [bucket]
            if not isinstance(tags, list):
                tags = [bucket]
            tags = [t for t in tags if isinstance(t, str) and t in ENTITY_BUCKETS]
            if not tags:
                tags = [bucket]
            entry: dict[str, Any] = {"tags": tags, "content": content.strip()}
            for opt in ("source_doc", "section"):
                v = item.get(opt)
                if isinstance(v, str) and v.strip():
                    entry[opt] = v.strip()
            cleaned.append(entry)
        out[bucket] = cleaned
    return out
