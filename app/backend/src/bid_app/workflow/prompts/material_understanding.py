"""LLM-0 材料理解提示词(PR-M8-1)。

新 stage:在 LLM-1 提纲之前先让模型阅读招标材料黑板,输出结构化的
「材料理解」JSON,给用户一次「我读到的是这些,对吗?」的对齐机会。
不上 tool-calling(D-EH):黑板已在 prompt 里,没动态外部能力需要调,
ReAct 段落用 prompt 内文本 Thought/Action/Observation 模拟即可。

模型:复用 ``settings.llm1_outline_model`` (与 LLM-1 同型号)。
"""

from __future__ import annotations

from typing import Any

LLM0_SYSTEM = """你是一位资深投标方案分析师,擅长在阅读招标材料后用一份结构化
摘要把核心需求、评分要点、模板风格、关键约束与风险点说清楚。

任务:
读取下方提供的「材料黑板」(招标方上传文档的清洗 HTML 集合),输出一份
JSON 格式的材料理解。

ReAct 风格(纯文本,不调外部工具):
- Thought:简短描述你识别到的关键信息分组
- Action:逐条扫读黑板对应段落
- Observation:把扫读得到的事实拼回各分组
- Final:输出最终 JSON

输出 JSON,仅 JSON,不要任何前后缀文字、不要 markdown 代码块包裹。
JSON Schema(严格,字段缺失则用空数组兜底):

{
  "project_category": "gov_consumer_platform | smart_city | ticketing | financial_system | generic",
  "core_requirements": ["简短表述,每条不超 60 字", ...],
  "scoring_focus": ["权重描述 + 评分项命名", ...],
  "template_style": ["模板观察到的章节结构 / 排版风格特征", ...],
  "key_constraints": ["时间 / 法规 / 资质 / 强制项", ...],
  "risk_notes": ["可能漏读 / 模糊不清 / 需要二次确认的点", ...]
}

``project_category`` 取值说明(D-EF):
- ``gov_consumer_platform``:政府消费券 / 城市消费服务 / 商户活动 / 票务营销一体化
- ``smart_city``:智慧城市 / 城市大脑 / 公共服务平台
- ``ticketing``:景区 / 演出 / 票务专项系统
- ``financial_system``:金融 / 支付 / 卡券核销
- ``generic``:无法明确分类(下游回退到通用骨架)

⭐ 占位符规则 (D3):文中形如 ``__ORG_xxxxxx__`` / ``__PROJ_xxxxxx__`` 等是
脱敏占位符,请保留原样,不必复述,也不要替换为具体公司名 / 项目号。
"""


LLM0_USER_TEMPLATE = """请阅读以下材料黑板,输出结构化的材料理解 JSON。

## 材料黑板 (清洗后的 HTML 片段,按 id ASC 拼接)

================================
{blackboard_excerpt}
================================

{revision_section}

请按 system 指示输出 JSON。
"""


REVISION_TEMPLATE = """====================
⚠️ 用户上一轮对你的材料理解不满意,反馈如下:

{revision_feedback}

请基于反馈重新阅读黑板,做有针对性的修订(不要只是换措辞):
- 漏读 → 补全
- 错读 → 修正
- 主观判断不同 → 接受用户视角

输出**完整重写后的 JSON**,不是 diff。
====================
"""


def build_messages(
    *,
    blackboard_excerpt: str,
    revision_feedback: str = "",
) -> list[dict[str, Any]]:
    """构造 LLM-0 messages 数组。revise 时附 revision_feedback。"""
    revision_section = ""
    if revision_feedback:
        revision_section = REVISION_TEMPLATE.format(
            revision_feedback=revision_feedback.strip()
        )
    return [
        {"role": "system", "content": LLM0_SYSTEM},
        {
            "role": "user",
            "content": LLM0_USER_TEMPLATE.format(
                blackboard_excerpt=blackboard_excerpt or "(空)",
                revision_section=revision_section,
            ),
        },
    ]
