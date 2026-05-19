"""D-EG chapter_type 分流测试。"""
from __future__ import annotations

from bid_app.workflow.prompts import (
    write_architecture_prompt,
    write_chapter_prompt,
    write_meeting_prompt,
    write_module_prompt,
    write_principle_prompt,
)
from bid_app.workflow.renderers import render
from bid_app.workflow.templates import load_pack


# ============== renderers (image_only / table_only) ==============


def test_render_qualification_cert() -> None:
    chapter = {
        "section": "7.1",
        "title": "信息安全管理体系认证证书",
        "chapter_type": "image_only",
        "template_slot": "qualification_cert",
    }
    text = render(chapter, None)
    assert text.startswith("## 7.1 信息安全管理体系认证证书")
    assert text.count("[此处放置") == 2  # 正本 + 副本
    assert "正本" in text and "副本" in text


def test_render_pm_resume_includes_table_and_cert_slots() -> None:
    chapter = {
        "section": "8.1",
        "title": "项目经理-周星亮",
        "chapter_type": "image_only",
        "template_slot": "pm_resume",
    }
    text = render(chapter, None)
    assert "## 8.1 项目经理-周星亮" in text
    assert "| 项 | 内容 | 项 | 内容 |" in text
    assert "PMP 证书" in text


def test_render_core_team_table_has_7_columns() -> None:
    chapter = {
        "section": "8.2",
        "title": "核心团队人员",
        "chapter_type": "table_only",
        "template_slot": "core_team_table",
    }
    text = render(chapter, None)
    assert "| 序号 | 本项目任职 | 姓名 | 职称 | 专业 | 执业或职业资格证明 | 备注 |" in text


def test_render_deviation_table_includes_three_notes_and_signoff() -> None:
    chapter = {
        "section": "9.1",
        "title": "技术/商务响应与偏离表",
        "chapter_type": "table_only",
        "template_slot": "deviation_table",
    }
    text = render(chapter, None)
    assert "磋商文件条目号" in text
    assert "1. \"响应与偏离\"应注明" in text
    assert "2. 属磋商文件规定" in text
    assert "3. 若全部条款无偏离" in text
    assert "供应商名称:" in text
    assert "日      期:" in text


def test_render_performance_case_uses_four_h3_slots_from_pack() -> None:
    pack = load_pack("gov_consumer_platform_v1")
    chapter = {
        "section": "6.1",
        "title": "案例1:2023客户运营服务合同",
        "chapter_type": "image_only",
        "template_slot": "performance_case",
    }
    text = render(chapter, pack)
    assert text.count("### ") == 4
    assert "合同服务内容页" in text
    assert "合同金额页" in text


def test_render_review_index_has_4_columns() -> None:
    chapter = {
        "section": "1",
        "title": "评审索引表",
        "chapter_type": "table_only",
        "template_slot": "review_index",
    }
    text = render(chapter, None)
    assert "| 序号 | 评审因素 | 应答值 | 应答文件对应页码 |" in text


# ============== chapter_type prompt 系统提示词 ==============


def test_module_prompt_has_three_section_anchors() -> None:
    sys_p = write_module_prompt.SYSTEM
    # 三段式锚点
    assert "### 技术实现" in sys_p
    assert "### 关键适配" in sys_p
    assert "### 典型业务流程" in sys_p
    # 流程三要素
    assert "流程目标" in sys_p
    assert "处理步骤" in sys_p
    assert "关键控制点" in sys_p
    # 时序图锚点(供 gen_visuals 扫描)
    assert "对应时序图:" in sys_p


def test_principle_prompt_uses_全角顿号_numbering() -> None:
    sys_p = write_principle_prompt.SYSTEM
    assert "1、" in sys_p and "2、" in sys_p
    assert "原则名" in sys_p


def test_architecture_prompt_emits_arch_anchor() -> None:
    sys_p = write_architecture_prompt.SYSTEM
    assert "对应架构图:总体架构" in sys_p
    assert "required_anchors" in sys_p


def test_meeting_prompt_lists_four_elements() -> None:
    sys_p = write_meeting_prompt.SYSTEM
    for key in ("会议目标", "日期与时间", "参加人员", "主要议程及责任"):
        assert key in sys_p


# ============== build_messages 接受 system_override / extra_user_directives ==============


def test_build_messages_uses_system_override() -> None:
    chapter = {
        "section": "3.2.2.1",
        "title": "用户认证与权限管理模块",
        "chapter_type": "module",
        "key_points": ["JWT 鉴权", "RBAC"],
        "target_pages": 3,
    }
    messages = write_chapter_prompt.build_messages(
        chapter=chapter,
        tech_spec_md="(空)",
        scoring_md="(空)",
        system_override=write_module_prompt.SYSTEM,
        extra_user_directives="## 本章硬约束(再次提醒)\n- 必须依次写三段式",
    )
    assert messages[0]["role"] == "system"
    # 注入了 module system
    assert "三段式" in messages[0]["content"] or "### 技术实现" in messages[0]["content"]
    # extra_user_directives 拼到 user 末尾
    assert "本章硬约束" in messages[1]["content"]


def test_build_messages_chapter_type_field_appears_in_user_message() -> None:
    chapter = {
        "section": "3.2.1",
        "title": "总体设计原则",
        "chapter_type": "principle",
        "key_points": ["x"],
        "target_pages": 2,
    }
    messages = write_chapter_prompt.build_messages(
        chapter=chapter,
        tech_spec_md="",
        scoring_md="",
    )
    assert "**章节类型**: principle" in messages[1]["content"]


def test_build_messages_default_system_when_no_override() -> None:
    """system_override=None 时仍走默认 LLM2_SYSTEM(normal 兜底)。"""
    messages = write_chapter_prompt.build_messages(
        chapter={"section": "1", "title": "T", "chapter_type": "normal", "target_pages": 1},
        tech_spec_md="",
        scoring_md="",
    )
    assert messages[0]["content"] == write_chapter_prompt.LLM2_SYSTEM


# ============== Phase 2C tool calling 提示注入 ==============


def test_build_messages_includes_search_blackboard_hint_when_tool_enabled() -> None:
    """tool_calling_enabled=True 时,user content 必须包含 search_blackboard 指引。"""
    messages = write_chapter_prompt.build_messages(
        chapter={
            "section": "3.2.2.1",
            "title": "用户认证模块",
            "chapter_type": "module",
            "key_points": ["x"],
            "target_pages": 3,
        },
        tech_spec_md="",
        scoring_md="",
        tool_calling_enabled=True,
    )
    user = messages[1]["content"]
    assert "search_blackboard" in user
    # 提示里说明应该 0-2 次,告诉模型别循环
    assert "0-2 次" in user or "0-2次" in user
    # 调用后下一条 message 应当是完整 Markdown,无 tool_call
    assert "完整的本章" in user or "完整 Markdown" in user or "完整的本章 Markdown" in user


def test_build_messages_no_tool_hint_when_disabled() -> None:
    messages = write_chapter_prompt.build_messages(
        chapter={
            "section": "1",
            "title": "T",
            "chapter_type": "normal",
            "target_pages": 1,
        },
        tech_spec_md="",
        scoring_md="",
        tool_calling_enabled=False,
    )
    assert "search_blackboard" not in messages[1]["content"]
