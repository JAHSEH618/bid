"""非 LLM 章节渲染器(D-EG)。

``chapter_type=image_only`` 与 ``chapter_type=table_only`` 的章节不调
LLM-2 — 直接根据 ``template_slot`` 与骨架配置渲染固定 markdown 骨架,
图位 / 表位用占位符标出,后续由用户在前端补图或由 assemble 节点据上下文
填表。

模块对外只暴露 ``render(chapter, pack)`` 一个函数。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_RendererFn = Callable[[str, str], str]


def _h2(section: str, title: str) -> str:
    return f"## {section} {title}"


def _h3(title: str) -> str:
    return f"### {title}"


def _image_placeholder(label: str) -> str:
    return f"> [此处放置 {label} 的扫描件或图片,由用户在前端上传]"


def _table_placeholder(headers: list[str], rows: int = 1) -> str:
    """渲染一张空 Markdown 表(只有表头 + N 个空行)。"""
    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "|" + "|".join(["---"] * len(headers)) + "|"
    body_rows = ["| " + " | ".join([""] * len(headers)) + " |" for _ in range(rows)]
    return "\n".join([header_row, sep_row, *body_rows])


def _render_performance_case(section: str, title: str, h3_slots: list[str]) -> str:
    """业绩案例:1 个 H2 + 4 个 H3 + 4 个图位。"""
    parts = [_h2(section, title), ""]
    for slot_name in h3_slots:
        parts.append(_h3(slot_name))
        parts.append("")
        parts.append(_image_placeholder(slot_name))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _render_qualification_cert(section: str, title: str) -> str:
    """资质证书:1 个 H2 + 正副本两张图位。"""
    parts = [
        _h2(section, title),
        "",
        _image_placeholder(f"{title} 正本"),
        "",
        _image_placeholder(f"{title} 副本"),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_pm_resume(section: str, title: str) -> str:
    """项目经理简历:H2 + 简历表(11 行 4 列空表)+ 证书图位。"""
    parts = [
        _h2(section, title),
        "",
        _table_placeholder(["项", "内容", "项", "内容"], rows=10),
        "",
        _image_placeholder("项目经理 PMP 证书"),
        "",
        _image_placeholder("项目经理身份证 / 学位证(按招标要求)"),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_core_team_table(section: str, title: str) -> str:
    """核心团队 17 行 7 列表(rule.md §8)。"""
    parts = [
        _h2(section, title),
        "",
        _table_placeholder(
            [
                "序号",
                "本项目任职",
                "姓名",
                "职称",
                "专业",
                "执业或职业资格证明",
                "备注",
            ],
            rows=15,
        ),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_core_member_cert(section: str, title: str) -> str:
    """核心成员证书(H3 标题已含姓名)- 2-4 张证书图位。"""
    parts = [
        _h2(section, title),
        "",
        _image_placeholder(f"{title} - 证书 1"),
        "",
        _image_placeholder(f"{title} - 证书 2"),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_review_index(section: str, title: str) -> str:
    """评审索引表(rule.md §1)。"""
    parts = [
        _h2(section, title),
        "",
        _table_placeholder(["序号", "评审因素", "应答值", "应答文件对应页码"], rows=8),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_compliance_index(section: str, title: str) -> str:
    """评审细项表(rule.md §1)。"""
    parts = [
        _h2(section, title),
        "",
        _table_placeholder(["序号", "评审细项", "细项应答"], rows=8),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _render_deviation_table(section: str, title: str) -> str:
    """技术/商务响应与偏离表(rule.md §9)— 表 + 三条说明 + 落款。"""
    parts = [
        _h2(section, title),
        "",
        _table_placeholder(
            [
                "序号",
                "磋商文件条目号",
                "采购规格/商务条款",
                "响应文件的规格/商务条款",
                "响应与偏离",
                "说明",
            ],
            rows=1,
        ),
        "",
        "说明:",
        "",
        '1. "响应与偏离"应注明"响应"或"偏离"。',
        "",
        '2. 属磋商文件规定可能变动的内容在"说明"栏中注明。',
        "",
        '3. 若全部条款无偏离,供应商需承诺"全部响应磋商文件商务和技术条款及合同条款的要求,无偏离"。',
        "",
        "供应商名称: __ORG_xxxxxx__",
        "",
        "日      期:    YYYY   年   MM   月   DD   日",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


_DISPATCH_IMAGE: dict[str, _RendererFn] = {
    "qualification_cert": _render_qualification_cert,
    "pm_resume": _render_pm_resume,
    "core_member_certs": _render_core_member_cert,
}


_DISPATCH_TABLE: dict[str, _RendererFn] = {
    "review_index": _render_review_index,
    "compliance_index": _render_compliance_index,
    "deviation_table": _render_deviation_table,
    "core_team_table": _render_core_team_table,
}


def render(chapter: dict[str, Any], pack: dict[str, Any] | None) -> str:
    """根据 chapter_type / template_slot 渲染固定骨架。

    pack 用于查询 ``performance_case_h3_slots`` 等需要骨架配置的渲染器;
    None 时使用 rule.md 默认值。
    """
    section = str(chapter.get("section") or "1")
    title = str(chapter.get("title") or "")
    slot = str(chapter.get("template_slot") or "")
    chapter_type = str(chapter.get("chapter_type") or "")

    if chapter_type == "image_only":
        # 业绩案例 — 走 expandable 展开后的叶子;每个案例渲 4 个 H3
        if slot in ("performance_case", "performance_cases"):
            h3_slots = (pack or {}).get("performance_case_h3_slots") or [
                "合同服务内容页",
                "合同附件1-技术规范书",
                "合同金额页",
                "合同签订时间页、合同签页",
            ]
            return _render_performance_case(section, title, h3_slots)
        renderer = _DISPATCH_IMAGE.get(slot)
        if renderer is not None:
            return renderer(section, title)
        # 通用 image_only:1 个图位兜底
        return _h2(section, title) + "\n\n" + _image_placeholder(title) + "\n"

    if chapter_type == "table_only":
        renderer = _DISPATCH_TABLE.get(slot)
        if renderer is not None:
            return renderer(section, title)
        # 通用 table_only:一张 4 列空表兜底
        return (
            _h2(section, title)
            + "\n\n"
            + _table_placeholder(["列 1", "列 2", "列 3", "列 4"], rows=1)
            + "\n"
        )

    # 非模板章节不该走到这里,但保留兜底返回章主标题占位
    return _h2(section, title) + "\n"
