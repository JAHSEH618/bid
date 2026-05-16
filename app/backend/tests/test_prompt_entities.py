"""Phase 1B: outline_prompt / write_chapter_prompt 接实体桶 +
回退到 markdown 截断的覆盖测试。"""
from __future__ import annotations

from bid_app.workflow.prompts.categorize_blackboard import (
    has_any_entries,
    render_buckets_for_prompt,
)
from bid_app.workflow.prompts.outline_prompt import build_messages as build_outline
from bid_app.workflow.prompts.write_chapter_prompt import (
    _pick_buckets_for_chapter,
)
from bid_app.workflow.prompts.write_chapter_prompt import (
    build_messages as build_chapter,
)


def test_has_any_entries() -> None:
    assert not has_any_entries(None)
    assert not has_any_entries({})
    assert not has_any_entries({"scoring_rules": []})
    assert not has_any_entries({"scoring_rules": [{"content": ""}]})
    assert has_any_entries({"scoring_rules": [{"content": "权重 30%"}]})


def test_render_buckets_basic() -> None:
    entities = {
        "scoring_rules": [
            {"tags": ["scoring_rules"], "content": "技术方案 50 分,商务 30 分"}
        ],
        "risk_signals": [],
    }
    out = render_buckets_for_prompt(
        entities, bucket_filter=["scoring_rules", "risk_signals"]
    )
    assert "scoring_rules" in out
    assert "技术方案 50 分" in out
    assert "本项目无相关条目" in out  # risk_signals 空桶占位


def test_render_buckets_truncates_per_bucket() -> None:
    big = "X" * 6000
    entities = {
        "scoring_rules": [
            {"content": big},
        ]
    }
    out = render_buckets_for_prompt(
        entities, bucket_filter=["scoring_rules"], per_bucket_char_limit=2000
    )
    assert "已截断" in out


def test_render_buckets_returns_placeholder_when_empty() -> None:
    assert render_buckets_for_prompt(None) == "(实体黑板暂未生成或为空)"
    assert render_buckets_for_prompt({}) == "(实体黑板暂未生成或为空)"


def test_outline_prompt_uses_entities_when_given() -> None:
    entities = {
        "scoring_rules": [{"content": "技术 60 分,商务 40 分"}],
        "technical_requirements": [{"content": "SLA ≥ 99.95%"}],
    }
    msgs = build_outline(
        tech_spec_md="技术需求原文",
        scoring_md="打分原文",
        template_md="模板原文",
        blackboard_entities=entities,
    )
    assert len(msgs) == 2
    user = msgs[1]["content"]
    # 走结构化桶路径 → user prompt 里有桶 label,没有原 markdown
    assert "实体桶" in user
    assert "技术 60 分" in user
    assert "技术需求原文" not in user
    assert "打分原文" not in user


def test_outline_prompt_falls_back_to_markdown_when_no_entities() -> None:
    msgs = build_outline(
        tech_spec_md="技术需求原文",
        scoring_md="打分原文",
        template_md="模板原文",
        blackboard_entities=None,
    )
    user = msgs[1]["content"]
    # 回退路径走老 user template,原 markdown 出现
    assert "技术需求原文" in user
    assert "打分原文" in user


def test_outline_prompt_falls_back_when_entities_all_empty() -> None:
    """10 桶都给但全是空数组也算 has_any_entries=False。"""
    empty_entities: dict[str, list] = {
        "project_info": [],
        "scoring_rules": [],
    }
    msgs = build_outline(
        tech_spec_md="原文 X",
        scoring_md="原文 Y",
        template_md="",
        blackboard_entities=empty_entities,
    )
    user = msgs[1]["content"]
    assert "原文 X" in user
    assert "原文 Y" in user


def test_pick_buckets_for_chapter_risk_keywords() -> None:
    chapter = {"title": "3.2 风险管控体系", "parent_titles": []}
    buckets = _pick_buckets_for_chapter(chapter)
    assert "risk_signals" in buckets
    assert "technical_requirements" in buckets or "compliance_constraints" in buckets


def test_pick_buckets_for_chapter_personnel() -> None:
    chapter = {"title": "5.1 项目经理与核心团队", "parent_titles": []}
    buckets = _pick_buckets_for_chapter(chapter)
    assert "personnel_info" in buckets


def test_pick_buckets_fallback() -> None:
    """没命中任何关键字 → 回到 scoring_rules + technical_requirements。"""
    chapter = {"title": "随便瞎写的奇怪标题", "parent_titles": []}
    buckets = _pick_buckets_for_chapter(chapter)
    assert "scoring_rules" in buckets
    assert "technical_requirements" in buckets


def test_chapter_prompt_uses_entities_when_given() -> None:
    entities = {
        "risk_signals": [{"content": "缺章节扣 8 分"}],
        "technical_requirements": [{"content": "RTO ≤ 4h"}],
    }
    chapter = {
        "title": "3.2 风险管控",
        "section": "3.2",
        "key_points": ["闭环 PDCA", "三级质检"],
        "target_pages": 3,
    }
    msgs = build_chapter(
        chapter=chapter,
        tech_spec_md="原 tech md",
        scoring_md="原 scoring md",
        blackboard_entities=entities,
    )
    user = msgs[1]["content"]
    assert "实体黑板" in user
    assert "RTO ≤ 4h" in user or "缺章节扣 8 分" in user
    # 不走 markdown 截断路径
    assert "原 tech md" not in user


def test_chapter_prompt_falls_back_to_markdown() -> None:
    chapter = {"title": "1 项目背景", "section": "1", "key_points": [], "target_pages": 2}
    msgs = build_chapter(
        chapter=chapter,
        tech_spec_md="一段技术需求摘要",
        scoring_md="一段打分规则",
        blackboard_entities=None,
    )
    user = msgs[1]["content"]
    assert "一段技术需求摘要" in user
    assert "一段打分规则" in user
