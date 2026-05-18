"""模版章节结构化校验器(D-EI / Stage 4)。

merge_chapter 节点把 LLM-2 正文 + LLM-3 视觉块拼成 ``full_chapter`` markdown
之后,调本模块跑一遍硬规则:三段式 / 流程三要素 / 七层架构关键词 / 资质 H3
齐全 / 偏离表三条说明 + 落款齐全 / 文体黑名单 / 段长 p90 等。

校验结果以 ``list[ValidationIssue]`` 返回:

- ``severity=error`` 命中且重试额度未用完 → 工作流自动 ``revise`` 一次,
  ``hint`` 拼到 ``revision_feedback`` 给 LLM-2;
- ``severity=warn`` 仅作前端提示,不触发自动 revise;
- 无任何 error 命中 → 直接进入 ``human_review``。

每条规则保持 ≤ 30 行,纯字符串 / 正则匹配,执行 < 50ms。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationIssue:
    """校验失败描述。``hint`` 会作为 revise feedback 喂回 LLM-2。"""

    code: str
    severity: str  # "error" | "warn"
    message: str
    hint: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_FlowRequiredKeywords = ("流程目标", "处理步骤", "关键控制点")
_SEQ_ANCHOR_RE = re.compile(r"^[ \t]*对应时序图[::]\s*(.+?)[ \t]*$", re.MULTILINE)
_ARCH_ANCHOR_RE = re.compile(r"^[ \t]*对应架构图[::]\s*(.+?)[ \t]*$", re.MULTILINE)
_PRINCIPLE_ITEM_RE = re.compile(r"^\s*([0-9]+)、\s*([^:\n]+?)[::]?\s*$", re.MULTILINE)
_H3_LINE_RE = re.compile(r"^[ \t]*###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_FORBIDDEN_MARKERS_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])("
    r"[一二三四五六七八九十]+、"  # 一、二、
    r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]"  # 圆圈数字
    r"|[◆▶●■◇▷○]"  # 黑名单符号
    r"|[✓✗]"
    r")"
)


def _check_module_three_section(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """三段式锚点齐全。"""
    missing = [k for k in ("技术实现", "关键适配", "典型业务流程") if k not in text]
    if not missing:
        return []
    return [
        ValidationIssue(
            code="module_three_section",
            severity="error",
            message=f"模块章三段式缺失: {' / '.join(missing)}",
            hint=(
                "请补全模块章的三段式锚点,**依次**写出 `### 技术实现` / "
                "`### 关键适配` / `### 典型业务流程` 三个三级标题,缺一不可。"
            ),
        )
    ]


def _check_flow_three_elements(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """每个 `对应时序图:` 锚点之前的段落必含三关键词。"""
    issues: list[ValidationIssue] = []
    for match in _SEQ_ANCHOR_RE.finditer(text):
        flow_name = match.group(1).strip()
        # 锚点行所在段往前找一段:从锚点位置向前找最近的 `\n\n`,前一段就是流程描述段
        start = text.rfind("\n\n", 0, match.start())
        para_start = text.rfind("\n\n", 0, start if start > 0 else 0)
        para_start = (para_start + 2) if para_start >= 0 else 0
        para = text[para_start : match.start()]
        missing = [k for k in _FlowRequiredKeywords if k not in para]
        if missing:
            issues.append(
                ValidationIssue(
                    code="flow_three_elements",
                    severity="error",
                    message=f"流程 `{flow_name}` 描述段缺少: {' / '.join(missing)}",
                    hint=(
                        f"流程 `{flow_name}` 的描述段必须依次包含 "
                        f"`流程目标`、`处理步骤`、`关键控制点` 三个关键词,"
                        f"格式见模板:`<流程名>。流程目标是...处理步骤为...关键控制点包括...`"
                    ),
                )
            )
    return issues


def _check_principle_count_and_names(text: str, chapter: dict[str, Any]) -> list[ValidationIssue]:
    """principle 章原则条数 + 名称对齐 required_anchors。"""
    required: list[str] = list(chapter.get("required_anchors") or [])
    if not required:
        return []
    found = [m.group(2).strip() for m in _PRINCIPLE_ITEM_RE.finditer(text)]
    if len(found) != len(required):
        return [
            ValidationIssue(
                code="principle_count",
                severity="error",
                message=f"原则条数 {len(found)} ≠ 预期 {len(required)}",
                hint=(
                    f"原则数量必须严格等于 ``required_anchors`` 给的 {len(required)} 条,"
                    f"且名称依次为 {required}。每条以 `N、<名称>:` 开头。"
                ),
            )
        ]
    issues: list[ValidationIssue] = []
    for i, (want, got) in enumerate(zip(required, found, strict=False)):
        if want not in got and got not in want:
            issues.append(
                ValidationIssue(
                    code="principle_name_mismatch",
                    severity="error",
                    message=f"第 {i + 1} 条原则名 `{got}` 与预期 `{want}` 不符",
                    hint=(
                        f"第 {i + 1} 条原则名必须严格写作 `{want}`,不要自己想新名字"
                        f"也不要替换为同义词。"
                    ),
                )
            )
    return issues


def _check_architecture_layers(text: str, chapter: dict[str, Any]) -> list[ValidationIssue]:
    """architecture 章 required_anchors 层名 100% 命中 + 架构图锚点存在。"""
    issues: list[ValidationIssue] = []
    required: list[str] = list(chapter.get("required_anchors") or [])
    missing = [layer for layer in required if layer not in text]
    if missing:
        issues.append(
            ValidationIssue(
                code="architecture_layers_missing",
                severity="error",
                message=f"架构层名缺失: {' / '.join(missing)}",
                hint=(
                    f"应用架构方案章必须明确列出全部 {len(required)} 个层名: "
                    f"{required}。漏掉的层名请补到正文中(每层各一段独立描述)。"
                ),
            )
        )
    if not _ARCH_ANCHOR_RE.search(text):
        issues.append(
            ValidationIssue(
                code="architecture_anchor_missing",
                severity="error",
                message="章末缺少 `对应架构图:总体架构` 锚点",
                hint=(
                    "请在架构方案章的末尾**单独一行**写 `对应架构图:总体架构`,"
                    "下游会据此触发架构图生成。"
                ),
            )
        )
    return issues


def _check_meeting_four_elements(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """会议章每个会议描述必含四要素。规则较宽松:全章必须出现这四个关键词。"""
    missing = [k for k in ("会议目标", "日期与时间", "参加人员", "主要议程及责任") if k not in text]
    if not missing:
        return []
    return [
        ValidationIssue(
            code="meeting_four_elements",
            severity="error",
            message=f"会议四要素缺失: {' / '.join(missing)}",
            hint=(
                "每个会议描述必须依次给出 `会议目标`、`日期与时间`、`参加人员`、"
                "`主要议程及责任` 四要素,每个独占一行,使用全角冒号 ``:``。"
            ),
        )
    ]


def _check_image_only_h3_complete(text: str, chapter: dict[str, Any]) -> list[ValidationIssue]:
    """image_only 章节的 H3 数量与骨架配置一致(目前主要校验 performance_case 的 4 H3)。"""
    slot = str(chapter.get("template_slot") or "")
    if slot not in ("performance_case", "performance_cases"):
        return []
    h3_count = len(_H3_LINE_RE.findall(text))
    if h3_count < 4:
        return [
            ValidationIssue(
                code="image_only_h3_missing",
                severity="error",
                message=f"业绩案例 H3 数 {h3_count} < 预期 4",
                hint="业绩案例章必须含 4 个 H3 子项(合同服务内容页 / 技术规范书 / 金额页 / 签订时间页)。",
            )
        ]
    return []


def _check_deviation_table_complete(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """偏离表必含表头、三条说明、落款两行。"""
    required_phrases = [
        "磋商文件条目号",
        "响应与偏离",
        '1. "响应与偏离"应注明',
        "2. 属磋商文件规定",
        "3. 若全部条款无偏离",
        "供应商名称:",
        "日      期:",
    ]
    missing = [p for p in required_phrases if p not in text]
    if not missing:
        return []
    return [
        ValidationIssue(
            code="deviation_table_incomplete",
            severity="error",
            message=f"偏离表缺少: {missing[0]} 等 {len(missing)} 项",
            hint=("技术/商务响应与偏离表必须含完整表头 + 三条说明 + 供应商名称/日期 两行落款。"),
        )
    ]


def _check_forbidden_list_markers(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """文体黑名单(D-EJ 子集):一二三、 / ①②③ / ◆▶● / ✓✗ 等。"""
    matches = _FORBIDDEN_MARKERS_RE.findall(text)
    if not matches:
        return []
    severity = "error" if len(matches) > 3 else "warn"
    return [
        ValidationIssue(
            code="forbidden_list_markers",
            severity=severity,
            message=f"出现 {len(matches)} 处禁用符号(如 `{matches[0]}`)",
            hint=(
                "列表编号只允许 `1.` / `1、` / `(1)`。禁止使用 `一、二、` / "
                "`①②③` / `◆▶●■` / `✓✗` / emoji。"
            ),
        )
    ]


def _check_paragraph_length_p90(text: str, _chapter: dict[str, Any]) -> list[ValidationIssue]:
    """段落 p90 ≤ 180 字(软警告)。"""
    paragraphs = [
        p.strip()
        for p in text.split("\n\n")
        if p.strip() and not p.lstrip().startswith(("#", "|", "```", ">", "-"))
    ]
    if len(paragraphs) < 5:
        return []
    lengths = [len(p) for p in paragraphs]
    p90 = sorted(lengths)[int(len(lengths) * 0.9)]
    if p90 <= 180:
        return []
    return [
        ValidationIssue(
            code="paragraph_too_long",
            severity="warn",
            message=f"段落 p90 长度 = {p90} 字(>180)",
            hint=("段落过长(p90 > 180 字),建议用 `###` 子节或编号列表拆分长段。"),
        )
    ]


# chapter_type → 适用规则集
_CHECKS_BY_TYPE: dict[str, list[Callable[[str, dict[str, Any]], list[ValidationIssue]]]] = {
    "module": [
        _check_module_three_section,
        _check_flow_three_elements,
        _check_forbidden_list_markers,
        _check_paragraph_length_p90,
    ],
    "principle": [
        _check_principle_count_and_names,
        _check_forbidden_list_markers,
    ],
    "architecture": [
        _check_architecture_layers,
        _check_forbidden_list_markers,
    ],
    "meeting": [
        _check_meeting_four_elements,
        _check_forbidden_list_markers,
    ],
    "image_only": [_check_image_only_h3_complete],
    "table_only": [_check_deviation_table_complete],
    "normal": [
        _check_forbidden_list_markers,
        _check_paragraph_length_p90,
    ],
}


def validate_chapter(text: str, chapter: dict[str, Any]) -> list[ValidationIssue]:
    """根据 ``chapter.chapter_type`` 选规则集运行;返回所有命中的 issue。"""
    chapter_type = str(chapter.get("chapter_type") or "normal")
    checks = _CHECKS_BY_TYPE.get(chapter_type) or _CHECKS_BY_TYPE["normal"]
    issues: list[ValidationIssue] = []
    for check in checks:
        try:
            issues.extend(check(text, chapter))
        except Exception:
            import structlog

            structlog.get_logger().exception(
                "template_validator_check_failed",
                check=check.__name__,
                chapter_type=chapter_type,
            )
    return issues


def issues_to_hint(issues: list[ValidationIssue]) -> str:
    """把 error 级 issue 拼成给 LLM-2 的 revise hint。"""
    errors = [i for i in issues if i.severity == "error"]
    if not errors:
        return ""
    lines = ["上一轮正文未通过模版校验,请按以下条目重写:"]
    for i, issue in enumerate(errors, 1):
        lines.append(f"{i}. {issue.message}")
        lines.append(f"   修复建议: {issue.hint}")
    return "\n".join(lines)


__all__ = [
    "ValidationIssue",
    "issues_to_hint",
    "validate_chapter",
]
