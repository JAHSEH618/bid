"""Markdown 后处理(R-17)。

LLM 偶尔会把段落紧挨着写(无空行分隔),markdown 渲染器把它们当作一段。
本模块兜底规范化:在写 ``Chapter.final_text`` / ``ChapterVersion.body_markdown``
之前过一遍,保证段落、标题、列表之间有合法空行。

不动:
- fenced code block(```...``` 或 ~~~...~~~ 内部)
- 表格连续行(``|`` 开头)
- 列表项内部(同级 - / 数字 项)

规则:
- 段落与段落之间 → 空行
- 段落与标题之间 → 标题前后空行
- 段落与列表之间 → 空行
- 段落与代码块/表格之间 → 空行
- 多个空行(``\\n\\n\\n+``)折叠成一个空行(``\\n\\n``)
"""
from __future__ import annotations

import re

# 代码围栏(```... 或 ~~~...),容忍 fence 后空格 + 语言名 + \r
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")
# 标题行 ## ... 或 # ...
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]")
# 无序列表行 - / * / +(后跟空格)
_UL_RE = re.compile(r"^[ \t]*[-*+][ \t]")
# 有序列表行 1. / 1) (后跟空格)
_OL_RE = re.compile(r"^[ \t]*\d+[.)][ \t]")
# 引用 > ...
_QUOTE_RE = re.compile(r"^[ \t]*>")
# 表格行(以 | 开头)
_TABLE_RE = re.compile(r"^[ \t]*\|")


def _line_kind(line: str) -> str:
    """把一行划成"种类",决定相邻两行是否需要空行分隔。"""
    if not line.strip():
        return "blank"
    if _HEADING_RE.match(line):
        return "heading"
    if _UL_RE.match(line) or _OL_RE.match(line):
        return "list"
    if _QUOTE_RE.match(line):
        return "quote"
    if _TABLE_RE.match(line):
        return "table"
    return "paragraph"


# 哪些 (prev, next) 组合**必须有空行**(除非已经空行了)
_NEEDS_BLANK = {
    ("paragraph", "heading"),
    ("paragraph", "list"),
    ("paragraph", "table"),
    ("paragraph", "quote"),
    ("paragraph", "paragraph"),  # 两段相邻 → 空行(R-17 主目标)
    ("heading", "list"),
    ("heading", "table"),
    ("heading", "paragraph"),
    ("heading", "quote"),
    ("list", "heading"),
    ("list", "paragraph"),
    ("list", "table"),
    ("list", "quote"),
    ("table", "heading"),
    ("table", "paragraph"),
    ("table", "list"),
    ("table", "quote"),
    ("quote", "heading"),
    ("quote", "paragraph"),
    ("quote", "list"),
    ("quote", "table"),
}


def normalize_markdown_paragraphs(text: str) -> str:
    """规范化 markdown 段落分隔。R-17。

    流程:
    1. 按行扫描,识别 fenced code block 进入 / 离开(里面任何字符不动)
    2. 在合适的相邻行间插入空行
    3. 折叠 ``\\n\\n\\n+`` 为 ``\\n\\n``
    """
    if not text:
        return text

    lines = text.splitlines()
    out: list[str] = []
    in_fence = False

    for i, line in enumerate(lines):
        # 围栏检测:进入 / 退出
        if _FENCE_RE.match(line):
            # 围栏行前 / 后(prose ↔ fenced)需要空行,但 fence 行本身保留原样
            if (
                not in_fence
                and out
                and out[-1].strip()
                and _line_kind(out[-1]) != "blank"
            ):
                # 进入 fence 前若上一行非空,补一行
                out.append("")
            out.append(line)
            in_fence = not in_fence
            continue

        if in_fence:
            # fence 内任何内容原样保留(包括空行)
            out.append(line)
            continue

        # 不在 fence:做相邻空行规则
        if not out:
            out.append(line)
            continue

        prev_line = out[-1]
        prev_kind = _line_kind(prev_line)
        cur_kind = _line_kind(line)

        if cur_kind == "blank" or prev_kind == "blank":
            out.append(line)
            continue

        if (prev_kind, cur_kind) in _NEEDS_BLANK:
            out.append("")  # 在两行之间插一个空行
        out.append(line)

    # 折叠多余连续空行(\n\n\n+ → \n\n)
    joined = "\n".join(out)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    # 确保末尾恰好 1 个 \n(写文件友好)
    return joined.rstrip("\n") + "\n"


# ============================================================================
# Mermaid 装饰色清理:LLM 偶尔在 mermaid block 里塞 `style A fill:#xxx`
# 等装饰色,会 override 前端 mermaid theme(用户已要求统一白底)。
# 在写入前 strip 掉这些行,让前端 themeVariables(commit 7426ff0)接管。
# ============================================================================

_MERMAID_FENCE_OPEN = re.compile(r"^[ \t]*```\s*mermaid\b", re.IGNORECASE)
_MERMAID_FENCE_CLOSE = re.compile(r"^[ \t]*```\s*$")
# 匹配 mermaid 节点装饰行:`style XX fill:...`、`classDef XX fill:...`、`class XX classname`
_MERMAID_STYLE_LINE = re.compile(
    r"^[ \t]*(?:style\s+\S+\s+fill:|classDef\s+\S+\s+fill:|class\s+[\w,\s]+\s+\w+\s*$)",
    re.IGNORECASE,
)


def strip_mermaid_decorations(text: str) -> str:
    """删除 mermaid 块内自定义颜色装饰行(让前端 theme 接管白底/中文/dark)。

    保留:节点定义、边、subgraph、注释、direction、note 等结构性语法
    删除:`style X fill:#abc`、`classDef X fill:...`、`class X colorName`
    """
    if not text or "```mermaid" not in text.lower():
        return text

    out: list[str] = []
    in_mermaid = False
    for line in text.splitlines():
        if not in_mermaid and _MERMAID_FENCE_OPEN.match(line):
            in_mermaid = True
            out.append(line)
            continue
        if in_mermaid and _MERMAID_FENCE_CLOSE.match(line):
            in_mermaid = False
            out.append(line)
            continue
        if in_mermaid and _MERMAID_STYLE_LINE.match(line):
            # 删掉装饰行(不输出)
            continue
        out.append(line)

    return "\n".join(out)


def postprocess_chapter_markdown(text: str) -> str:
    """章节 final_text / ChapterVersion.body_markdown 写入前的统一后处理入口。

    顺序:
    1. strip mermaid 装饰色(让 theme 白底接管)
    2. normalize 段落空行(R-17)
    """
    if not text:
        return text
    text = strip_mermaid_decorations(text)
    text = normalize_markdown_paragraphs(text)
    return text
