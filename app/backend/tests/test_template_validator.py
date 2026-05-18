"""D-EI Stage 4 模版校验器测试。"""
from __future__ import annotations

from bid_app.services.template_validator import (
    ValidationIssue,
    issues_to_hint,
    validate_chapter,
)


def _has_error(issues: list[ValidationIssue], code: str) -> bool:
    return any(i.code == code and i.severity == "error" for i in issues)


def _has_warn(issues: list[ValidationIssue], code: str) -> bool:
    return any(i.code == code and i.severity == "warn" for i in issues)


# ============== module 章规则 ==============


MODULE_OK = """## 3.2.2.1 用户认证模块

### 技术实现

建设统一认证中心,沉淀 JWT 鉴权。

### 关键适配

商户入口与用户入口隔离。

### 典型业务流程

1. 商户登录与权限装载流程。流程目标是确认权限。处理步骤为输入手机号、校验白名单、装载角色。关键控制点包括白名单拦截、停用账号即时失效。

对应时序图:商户登录与权限装载流程
"""


def test_module_chapter_passes_with_three_sections_and_flow_elements() -> None:
    issues = validate_chapter(
        MODULE_OK,
        {"chapter_type": "module"},
    )
    assert not [i for i in issues if i.severity == "error"]


def test_module_chapter_missing_section_anchor() -> None:
    text = MODULE_OK.replace("### 关键适配", "### 实施约束")
    issues = validate_chapter(text, {"chapter_type": "module"})
    assert _has_error(issues, "module_three_section")


def test_module_flow_missing_keyword() -> None:
    text = MODULE_OK.replace("关键控制点包括", "重点机制是")
    issues = validate_chapter(text, {"chapter_type": "module"})
    assert _has_error(issues, "flow_three_elements")


# ============== principle 章规则 ==============


PRINCIPLE_OK = """## 1 总体设计原则

本项目面向消费券业务,整体建设遵循统一平台原则。

原则如下:

1、开放性:
选用成熟开源技术栈和标准化接口规范构建系统。

2、灵活性与扩展性:
通过领域拆分和能力中心化设计。

3、稳定性:
采用分布式部署、读写分离、缓存削峰。

4、易维护性:
系统参数、活动规则均采用配置化管理。

5、安全性:
从身份认证、权限控制、敏感信息保护多个层面构建安全体系。
"""

PRINCIPLE_REQUIRED = ["开放性", "灵活性与扩展性", "稳定性", "易维护性", "安全性"]


def test_principle_five_items_match_required_anchors() -> None:
    issues = validate_chapter(
        PRINCIPLE_OK,
        {"chapter_type": "principle", "required_anchors": PRINCIPLE_REQUIRED},
    )
    assert not [i for i in issues if i.severity == "error"]


def test_principle_wrong_count() -> None:
    text = PRINCIPLE_OK.replace("5、安全性:\n从身份认证、权限控制、敏感信息保护多个层面构建安全体系。\n", "")
    issues = validate_chapter(
        text,
        {"chapter_type": "principle", "required_anchors": PRINCIPLE_REQUIRED},
    )
    assert _has_error(issues, "principle_count")


def test_principle_name_mismatch() -> None:
    text = PRINCIPLE_OK.replace("3、稳定性:", "3、可靠性:")
    issues = validate_chapter(
        text,
        {"chapter_type": "principle", "required_anchors": PRINCIPLE_REQUIRED},
    )
    assert _has_error(issues, "principle_name_mismatch")


# ============== architecture 章规则 ==============


ARCH_OK = """## 1 应用架构方案

系统整体采用接入层、网关层、业务服务层、能力中心层、集成接口层、数据服务层、基础设施层七层架构。

接入层承载多端接入。
网关层负责统一鉴权。
业务服务层承载交易。
能力中心层沉淀通用能力。
集成接口层负责外部对接。
数据服务层负责数据管理。
基础设施层负责支撑。

对应架构图:总体架构
"""

ARCH_REQUIRED = [
    "接入层", "网关层", "业务服务层", "能力中心层",
    "集成接口层", "数据服务层", "基础设施层",
]


def test_architecture_passes_with_all_layers() -> None:
    issues = validate_chapter(
        ARCH_OK,
        {"chapter_type": "architecture", "required_anchors": ARCH_REQUIRED},
    )
    assert not [i for i in issues if i.severity == "error"]


def test_architecture_missing_layer() -> None:
    text = ARCH_OK.replace("能力中心层", "中台层")
    issues = validate_chapter(
        text,
        {"chapter_type": "architecture", "required_anchors": ARCH_REQUIRED},
    )
    assert _has_error(issues, "architecture_layers_missing")


def test_architecture_missing_anchor() -> None:
    text = ARCH_OK.replace("对应架构图:总体架构", "")
    issues = validate_chapter(
        text,
        {"chapter_type": "architecture", "required_anchors": ARCH_REQUIRED},
    )
    assert _has_error(issues, "architecture_anchor_missing")


# ============== meeting 章规则 ==============


def test_meeting_passes_with_four_elements() -> None:
    text = """## 4.1 项目沟通管理

会议目标:沟通项目状态。

日期与时间:每周一上午 10 点。

参加人员:项目组核心成员。

主要议程及责任:更新状态、识别风险。
"""
    issues = validate_chapter(text, {"chapter_type": "meeting"})
    assert not [i for i in issues if i.severity == "error"]


def test_meeting_missing_element() -> None:
    text = "## 4.1 X\n\n会议目标:A\n\n日期与时间:B\n\n参加人员:C\n"  # 缺主要议程
    issues = validate_chapter(text, {"chapter_type": "meeting"})
    assert _has_error(issues, "meeting_four_elements")


# ============== deviation_table ==============


def test_deviation_table_missing_signoff() -> None:
    text = "## 9 偏离表\n\n| 序号 | 磋商文件条目号 | 响应与偏离 |\n|---|---|---|\n| 1 | A | 响应 |"
    issues = validate_chapter(
        text,
        {"chapter_type": "table_only", "template_slot": "deviation_table"},
    )
    assert _has_error(issues, "deviation_table_incomplete")


# ============== forbidden markers ==============


def test_forbidden_markers_warn_at_low_count() -> None:
    text = "## 1 X\n\n① 第一项\n\n② 第二项\n"
    issues = validate_chapter(text, {"chapter_type": "normal"})
    assert _has_warn(issues, "forbidden_list_markers")


def test_forbidden_markers_error_at_high_count() -> None:
    text = "## 1 X\n\n①②③④ 全班\n\n一、二、三、四、 也全班\n"
    issues = validate_chapter(text, {"chapter_type": "normal"})
    # 命中数 > 3 升级为 error
    assert _has_error(issues, "forbidden_list_markers")


# ============== issues_to_hint ==============


def test_issues_to_hint_aggregates_errors_only() -> None:
    issues = [
        ValidationIssue("a", "error", "missing X", "add X"),
        ValidationIssue("b", "warn", "minor Y", "fix Y"),
        ValidationIssue("c", "error", "missing Z", "add Z"),
    ]
    hint = issues_to_hint(issues)
    assert "missing X" in hint
    assert "missing Z" in hint
    assert "minor Y" not in hint  # warn 不进 hint


def test_issues_to_hint_empty_when_no_errors() -> None:
    issues = [ValidationIssue("a", "warn", "...", "...")]
    assert issues_to_hint(issues) == ""


# ============== image_only ==============


def test_image_only_performance_case_h3_complete() -> None:
    text = (
        "## 6.1 案例1\n\n"
        "### 合同服务内容页\n\n[图]\n\n"
        "### 合同附件1-技术规范书\n\n[图]\n\n"
        "### 合同金额页\n\n[图]\n\n"
        "### 合同签订时间页、合同签页\n\n[图]\n"
    )
    issues = validate_chapter(
        text,
        {"chapter_type": "image_only", "template_slot": "performance_case"},
    )
    assert not [i for i in issues if i.severity == "error"]


def test_image_only_performance_case_h3_missing() -> None:
    text = "## 6.1 案例1\n\n### 合同服务内容页\n\n[图]\n"
    issues = validate_chapter(
        text,
        {"chapter_type": "image_only", "template_slot": "performance_case"},
    )
    assert _has_error(issues, "image_only_h3_missing")
