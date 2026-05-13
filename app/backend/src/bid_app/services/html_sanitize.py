"""HTML 黑板的清洗 + markdown 转换工具 (PR-M7-3 / D2)。

外部输入 → markdown → HTML 时,先 ``markdown.markdown`` 转一遍,再走
``bleach.clean`` 白名单清洗,去掉 ``<script>`` / inline style / ``on*``
事件等危险节点。最终落盘的 HTML 是「可被 LLM prompt 引用的安全片段」。

白名单口径(IMPLEMENTATION_SPEC §24 / PR-M7-3):
- 标题:h1..h6
- 段落 / 文字:p / span / strong / em / br / blockquote
- 列表:ul / ol / li
- 表格:table / thead / tbody / tr / th / td
- 代码:pre / code
- 链接:a (rel/href only)
"""

from __future__ import annotations

import bleach
import markdown as md_lib

ALLOWED_TAGS: list[str] = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "span", "strong", "em", "br", "blockquote",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "pre", "code",
    "a",
]
"""白名单标签集合。下游 prompt 引用黑板时只会看到这些 tag。"""

ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "rel", "title"],
    "th": ["scope"],
    "td": ["colspan", "rowspan"],
}
"""仅保留无害属性,inline style / on* event handler 一律被吃掉。"""

ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto"]
"""链接协议白名单;javascript: / data: 等会被 bleach 丢弃。"""


def markdown_to_safe_html(md: str) -> str:
    """markdown → html → bleach 清洗,返回安全 HTML 字符串。

    - 空字符串 / None-ish 输入直接返回 ``""``。
    - bleach 默认会 strip 不在白名单的 tag,不抛异常。
    """
    if not md:
        return ""
    raw_html = md_lib.markdown(
        md,
        extensions=["tables", "fenced_code"],
        output_format="html",
    )
    cleaned = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return cleaned


def sanitize_html(html: str) -> str:
    """已经是 HTML 的输入直接走 bleach 清洗。"""
    if not html:
        return ""
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
