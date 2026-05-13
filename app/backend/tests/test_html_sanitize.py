"""PR-M7-3 tests:html_sanitize markdown → safe HTML 路径。"""

from __future__ import annotations

from bid_app.services.html_sanitize import (
    markdown_to_safe_html,
    sanitize_html,
)


def test_markdown_basic_renders() -> None:
    out = markdown_to_safe_html("# 标题\n\n正文段落")
    assert "<h1>" in out
    assert "<p>" in out
    assert "标题" in out


def test_markdown_table_preserved() -> None:
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    out = markdown_to_safe_html(md)
    assert "<table>" in out
    assert "<td>" in out


def test_script_tag_stripped() -> None:
    """``<script>`` tag must be stripped (bleach 白名单)。

    Bleach 的默认行为是去掉禁用 tag 但保留 inner text。我们这里只检验
    没有真正的 script 元素能注入到下游 HTML 中。
    """
    md = '正文\n\n<script>alert("xss")</script>\n\n后段'
    out = markdown_to_safe_html(md)
    assert "<script>" not in out
    assert "</script>" not in out
    # bleach 不会把 quote / paren 也吞掉,但执行风险已经消除


def test_inline_style_stripped() -> None:
    """`style=...` 属性不在白名单内,bleach 会去除。"""
    out = sanitize_html('<p style="color:red" onclick="x()">hi</p>')
    assert "style=" not in out
    assert "onclick" not in out
    assert "hi" in out


def test_javascript_link_dropped() -> None:
    """`href="javascript:..."` 被 bleach 丢弃 protocol。"""
    out = sanitize_html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in out


def test_safe_link_kept() -> None:
    out = sanitize_html('<a href="https://example.com">link</a>')
    assert 'href="https://example.com"' in out


def test_empty_input() -> None:
    assert markdown_to_safe_html("") == ""
    assert markdown_to_safe_html(None) == ""  # type: ignore[arg-type]
    assert sanitize_html("") == ""
