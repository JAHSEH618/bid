"""Tests for ``services.redaction`` (PR-M6-1 / D3)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bid_app.services.redaction import (
    PLACEHOLDER_RE,
    RedactionContext,
    load_rules,
    redact,
    redact_messages,
    reset_rules_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_rules_cache()


def test_empty_input_returns_empty() -> None:
    ctx = RedactionContext()
    assert redact("", ctx) == ""


def test_phone_redacted() -> None:
    ctx = RedactionContext()
    out = redact("联系电话 13800138000 请保密", ctx)
    assert "13800138000" not in out
    assert "__PHONE_" in out
    # 6 hex chars after kind prefix
    assert PLACEHOLDER_RE.search(out) is not None


def test_email_redacted() -> None:
    ctx = RedactionContext()
    out = redact("发到 admin@example.com 即可", ctx)
    assert "admin@example.com" not in out
    assert "__EMAIL_" in out


def test_idcard_redacted_before_phone() -> None:
    ctx = RedactionContext()
    # 身份证 18 位,首 11 位看上去像手机但顺序保证身份证先被吃掉
    text = "身份证 11010519491231002X 是合规要件"
    out = redact(text, ctx)
    assert "11010519491231002X" not in out
    assert "__IDCARD_" in out
    assert "__PHONE_" not in out


def test_project_number() -> None:
    ctx = RedactionContext()
    out = redact("本项目编号为 ABC-12345,请查询", ctx)
    assert "ABC-12345" not in out
    assert "__PROJ_" in out


def test_org_chinese_suffix() -> None:
    ctx = RedactionContext()
    out = redact("由中铁某局承建,某科技公司协助", ctx)
    assert "中铁某局" not in out
    assert "某科技公司" not in out
    assert out.count("__ORG_") == 2


def test_same_value_same_placeholder() -> None:
    """同一 request 内重复出现的「中铁某局」始终映射到同一占位符（doc 验收点）。"""
    ctx = RedactionContext()
    out = redact("中铁某局 牵头,中铁某局 负责实施", ctx)
    placeholders = PLACEHOLDER_RE.findall(out)
    assert len(placeholders) == 2
    assert placeholders[0] == placeholders[1]


def test_allowlist_skipped() -> None:
    ctx = RedactionContext(allowlist=frozenset({"中铁某局"}))
    out = redact("中铁某局 承建,某科技公司 协助", ctx)
    assert "中铁某局" in out  # allowlist 保留
    assert "某科技公司" not in out  # 仍脱敏


def test_messages_helper_does_not_mutate_input() -> None:
    """redact_messages 不应原地修改入参 dict。"""
    ctx = RedactionContext()
    original = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "我是 13800138000"},
    ]
    out = redact_messages(original, ctx)
    assert original[1]["content"] == "我是 13800138000"  # 入参不变
    assert "13800138000" not in out[1]["content"]
    assert out[0]["content"] == "你是助手"  # 不含敏感数据的消息原样


def test_non_string_content_passthrough() -> None:
    """multi-part / image content 等非字符串 content 不脱敏,原样透传。"""
    ctx = RedactionContext()
    original = [
        {"role": "user", "content": [{"type": "image_url", "url": "x"}]},
    ]
    out = redact_messages(original, ctx)
    assert out[0]["content"] == [{"type": "image_url", "url": "x"}]


def test_overlap_phone_then_project() -> None:
    """正则顺序 IDCARD → PHONE → EMAIL → PROJ → ORG,验证不会互相吃。"""
    ctx = RedactionContext()
    out = redact(
        "电话 13800138000 项目 PROJ-2024 邮箱 a@b.cn",
        ctx,
    )
    assert "13800138000" not in out
    assert "PROJ-2024" not in out
    assert "a@b.cn" not in out
    assert "__PHONE_" in out
    assert "__PROJ_" in out
    assert "__EMAIL_" in out


def test_already_placeholder_not_double_redacted() -> None:
    """已脱敏的占位符在二次 redact 中不会被再次替换。"""
    ctx = RedactionContext()
    out1 = redact("某科技公司 合作", ctx)
    out2 = redact(out1, ctx)
    assert out1 == out2


def test_context_items_only_exposes_placeholder() -> None:
    """RedactionContext.items() 不应泄露原值,只能拿到 (placeholder, kind)。"""
    ctx = RedactionContext()
    redact("电话 13800138000", ctx)
    items = ctx.items()
    assert len(items) == 1
    placeholder, kind = items[0]
    assert placeholder.startswith("__PHONE_")
    assert kind == "PHONE"
    # 没法从 items() 拿到 "13800138000"
    flattened = "".join(str(x) for pair in items for x in pair)
    assert "13800138000" not in flattened


def test_custom_yaml_path(tmp_path: Path) -> None:
    """``BID_APP_REDACTION_DICT_PATH`` 等效行为:传入 path 覆盖默认。"""
    custom = tmp_path / "rules.yaml"
    custom.write_text(
        textwrap.dedent(
            """
            patterns:
              idcard: '(?<!\\d)\\d{18}(?!\\d)'
              phone: '(?<!\\d)\\d{11}(?!\\d)'
              email: '\\S+@\\S+'
              project: 'P\\d{4}'
            org_suffixes:
              - 子公司
            default_allowlist: []
            """
        ).strip(),
        encoding="utf-8",
    )
    rules = load_rules(custom)
    ctx = RedactionContext()
    out = redact("P1234 由某子公司 牵头", ctx, rules=rules)
    assert "P1234" not in out
    assert "某子公司" not in out


def test_blackboard_untouched_smoke() -> None:
    """⭐ 验收点:黑板文件内仍是原文,只有出栈到 LLM 时替换。

    本测试只是占位:redaction 模块本身不接触黑板。真正的「黑板不脱敏」
    保证由 ``services/llm.py`` 的调用点提供 —— 它读 blackboard,然后
    把 messages 喂给 redact_messages,黑板文件不在 redact 链路上。
    """
    # 直接读不到黑板文件,只是确认 redact 不会越权写盘
    ctx = RedactionContext()
    redact("测试 13800138000", ctx)
    assert ctx.mapping  # 仅内存映射,没有 IO
