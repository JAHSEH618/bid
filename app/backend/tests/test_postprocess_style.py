"""D-EJ Stage 5 文体规范化测试。"""
from __future__ import annotations

from bid_app.workflow.postprocess import (
    normalize_style,
    postprocess_chapter_markdown,
)


def test_normalize_cn_numeral_prefix_replaces_with_arabic() -> None:
    text = "一、第一条要点\n二、第二条要点\n"
    out = normalize_style(text)
    assert "1. 第一条要点" in out
    assert "2. 第二条要点" in out
    assert "一、" not in out and "二、" not in out


def test_normalize_circled_number_prefix() -> None:
    text = "①第一项\n②第二项\n③第三项\n"
    out = normalize_style(text)
    assert "1. 第一项" in out
    assert "2. 第二项" in out
    assert "3. 第三项" in out


def test_normalize_bullet_decoration_replaced_with_dash() -> None:
    text = "◆ 黑名单符号\n▶ 不允许\n● 也不允许\n"
    out = normalize_style(text)
    assert out.startswith("- 黑名单符号")
    assert "- 不允许" in out
    assert "- 也不允许" in out


def test_normalize_cjk_ascii_spacing() -> None:
    text = "我们使用Spring Cloud部署15分钟SLA"
    out = normalize_style(text)
    assert "使用 Spring" in out
    assert "Cloud 部署" in out
    assert "部署 15" in out
    assert "15 分钟" in out
    assert "分钟 SLA" in out


def test_normalize_does_not_touch_code_fence_internal() -> None:
    text = "```python\nx=1\n# 一、中文注释 should NOT be touched\n```\n"
    out = normalize_style(text)
    assert "一、中文注释" in out  # 围栏内保留
    assert "1. 中文注释" not in out


def test_normalize_does_not_touch_table_rows() -> None:
    text = "| 一、列 | 二、列 |\n|---|---|\n| ① 数据 | ② 数据 |\n"
    out = normalize_style(text)
    # 表格行不改
    assert "一、列" in out
    assert "①" in out


def test_normalize_skips_mermaid_block_internal() -> None:
    text = "```mermaid\nflowchart TD\n  A[\"中文label\"] --> B\n```\n"
    out = normalize_style(text)
    # Mermaid 内不动
    assert "[\"中文label\"]" in out  # 不补空格


def test_normalize_paragraph_resets_counters() -> None:
    """空行后 `一、` 重新从 1 开始计。"""
    text = "一、A\n二、B\n\n一、C\n二、D\n"
    out = normalize_style(text)
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("1. ")
    assert lines[1].startswith("2. ")
    assert lines[2].startswith("1. ")
    assert lines[3].startswith("2. ")


def test_normalize_is_idempotent() -> None:
    text = "一、A\n二、B\n①c\n②d\n我们用Spring Cloud\n"
    once = normalize_style(text)
    twice = normalize_style(once)
    assert once == twice


def test_postprocess_chapter_markdown_applies_style_normalization() -> None:
    """端到端:postprocess_chapter_markdown 把禁用符号与中英空格都修掉。"""
    text = "## 1 标题\n\n一、要点A\n二、要点B用Spring Cloud实现\n"
    out = postprocess_chapter_markdown(text)
    # 中英空格也会作用在 `要点 A` 上
    assert "1. 要点 A" in out
    assert "2. 要点 B 用 Spring Cloud 实现" in out


def test_normalize_handles_two_digit_cn_numerals() -> None:
    text = "十、第十条\n十一、第十一条\n二十、第二十条\n"
    out = normalize_style(text)
    assert "10. 第十条" in out
    assert "11. 第十一条" in out
    assert "20. 第二十条" in out
