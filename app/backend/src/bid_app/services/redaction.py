"""脱敏服务（PR-M6-1 / D3）。

所有 LLM 调用前在出栈点替换敏感信息为占位符,不持久化映射。
- ``redact(text, ctx)``：对单段文本应用所有规则。
- ``RedactionContext``：request-scoped 缓存,保证同一调用内同名值得到同一
  占位符（``__KIND_xxx__``,xxx = sha1(value)[:6]）。
- 黑板 (``Project.blackboard_path``) 永远保存原文,仅在 LLM 出栈点替换。
  入栈不还原(D3 不可逆）—— 占位符直接进章节正文,UI 全链路 banner 提示。

规则覆盖（默认）：
- 正则：身份证 (18) / 手机 (11) / 邮箱 / 项目编号 ``[A-Z]{2,}-?\\d{4,}``
- 字典：公司后缀 (公司/集团/院/局/中心/研究所) 拼接中文前缀
- 项目级 allowlist：在 ``RedactionContext(allowlist=...)`` 传入跳过

YAML 配置可被 ``BID_APP_REDACTION_DICT_PATH`` 覆盖。
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ⭐ 规则顺序：IDCARD (18 位) → PHONE (11 位) → EMAIL → PROJ → ORG。
#   避免长串数字被先匹配掉短串的子串（11 位手机号被吃成身份证的前 11 位）。

PLACEHOLDER_RE = re.compile(r"__[A-Z]+_[0-9a-f]{6}__")
"""识别已经是占位符的文本片段（前端高亮 / banner 检测复用）。"""

# 默认 YAML 与服务包同目录,可被 BID_APP_REDACTION_DICT_PATH 覆盖。
_DEFAULT_RULES_FILE = Path(__file__).with_name("redaction_rules.yaml")


@dataclass
class RedactionRules:
    """从 YAML 加载的规则集合。"""

    idcard_pattern: re.Pattern[str]
    phone_pattern: re.Pattern[str]
    email_pattern: re.Pattern[str]
    project_pattern: re.Pattern[str]
    org_pattern: re.Pattern[str]
    default_allowlist: frozenset[str]


def _compile_rules(raw: dict[str, object]) -> RedactionRules:
    """编译 YAML 原始 dict 为 ``RedactionRules``。

    ``patterns`` 段是 regex 字符串字典;``org_suffixes`` 列表会被拼成一个
    `[一-龥]{2,12}(?:公司|集团|...)` 综合规则;``default_allowlist`` 列表
    转 frozenset。
    """
    patterns_obj = raw.get("patterns") or {}
    suffixes_obj = raw.get("org_suffixes") or []
    allowlist_obj = raw.get("default_allowlist") or []

    if not isinstance(patterns_obj, dict):
        raise ValueError("redaction_rules.yaml: `patterns` must be a mapping")
    if not isinstance(suffixes_obj, list):
        raise ValueError("redaction_rules.yaml: `org_suffixes` must be a list")
    if not isinstance(allowlist_obj, list):
        raise ValueError("redaction_rules.yaml: `default_allowlist` must be a list")

    patterns: dict[str, str] = {str(k): str(v) for k, v in patterns_obj.items()}
    suffixes: list[str] = [str(s) for s in suffixes_obj]
    allowlist: list[str] = [str(s) for s in allowlist_obj]

    for required in ("idcard", "phone", "email", "project"):
        if required not in patterns:
            raise ValueError(f"redaction_rules.yaml: missing `patterns.{required}`")
    if not suffixes:
        raise ValueError("redaction_rules.yaml: `org_suffixes` is empty")

    # ORG: Chinese-char prefix (2-12) + any 公司/集团/...
    suffix_alt = "|".join(re.escape(s) for s in suffixes)
    org_re = f"[一-龥]{{2,12}}(?:{suffix_alt})"

    return RedactionRules(
        idcard_pattern=re.compile(patterns["idcard"]),
        phone_pattern=re.compile(patterns["phone"]),
        email_pattern=re.compile(patterns["email"]),
        project_pattern=re.compile(patterns["project"]),
        org_pattern=re.compile(org_re),
        default_allowlist=frozenset(allowlist),
    )


_default_rules_cache: RedactionRules | None = None


def _resolved_default_path() -> Path:
    """根据 env 决定默认 YAML 路径,运维可通过 ``BID_APP_REDACTION_DICT_PATH`` 覆盖。

    直接读 ``os.environ`` 而不是走 ``Settings``,避免在测试环境(没设
    postgres/jwt 等必填)就让 redaction 模块 import 失败。``Settings`` 会单独
    通过同名字段读这个 env,两边读到的值一致。
    """
    override = os.environ.get("BID_APP_REDACTION_DICT_PATH")
    if override:
        return Path(override)
    return _DEFAULT_RULES_FILE


def load_rules(path: Path | None = None) -> RedactionRules:
    """加载 YAML 规则。无 path → 默认路径(env ``BID_APP_REDACTION_DICT_PATH`` 覆盖)。

    解析过的 ``RedactionRules`` 缓存到模块级,后续 ``load_rules()`` 重复调用
    返回同一对象;传入非默认 ``path`` 时绕过缓存,允许测试用临时文件。
    """
    global _default_rules_cache
    if path is None:
        if _default_rules_cache is not None:
            return _default_rules_cache
        path = _resolved_default_path()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = _compile_rules(raw)
        _default_rules_cache = rules
        return rules

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _compile_rules(raw)


def reset_rules_cache() -> None:
    """测试钩子：强制下次 ``load_rules`` 重新读 YAML（用于切换 env path）。"""
    global _default_rules_cache
    _default_rules_cache = None


@dataclass
class RedactionContext:
    """Request-scoped 脱敏上下文。

    - ``allowlist``：本次调用跳过脱敏的字面值集合(项目级白名单)。
    - ``mapping``：本次调用内部的 value → placeholder 缓存,保证同名值
      落同一占位符;调用结束自然 GC,不持久化。
    """

    allowlist: frozenset[str] = field(default_factory=frozenset)
    mapping: dict[str, str] = field(default_factory=dict)

    def placeholder(self, value: str, kind: str) -> str:
        """返回 ``__{KIND}_{hash6}__`` 占位符;allowlist 命中则原样返回。"""
        if value in self.allowlist:
            return value
        cached = self.mapping.get(value)
        if cached is not None:
            return cached
        h = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]
        placeholder = f"__{kind}_{h}__"
        self.mapping[value] = placeholder
        return placeholder

    def items(self) -> list[tuple[str, str]]:
        """返回本次调用内的 (placeholder, kind) 列表,UI 占位符清单抽屉用。

        注意:为避免泄露原值,只返回 placeholder 与解码出的 kind,不返回原值。
        """
        out: list[tuple[str, str]] = []
        for _value, placeholder in self.mapping.items():
            m = re.match(r"__([A-Z]+)_[0-9a-f]{6}__", placeholder)
            kind = m.group(1) if m else "UNKNOWN"
            out.append((placeholder, kind))
        return out


def redact(
    text: str,
    ctx: RedactionContext,
    *,
    rules: RedactionRules | None = None,
) -> str:
    """对单段文本应用所有规则,返回脱敏后字符串。

    - 空字符串 / None-ish 输入直接返回原值,避免下游模板拼接出 ``"None"``。
    - 规则顺序固定:IDCARD → PHONE → EMAIL → PROJ → ORG(避免长串吃短串)。
    - 已经是占位符 (``__XXX_xxx__``) 的片段不会被二次脱敏(regex 自然不命中)。
    """
    if not text:
        return text
    rules = rules or load_rules()

    text = rules.idcard_pattern.sub(lambda m: ctx.placeholder(m.group(0), "IDCARD"), text)
    text = rules.phone_pattern.sub(lambda m: ctx.placeholder(m.group(0), "PHONE"), text)
    text = rules.email_pattern.sub(lambda m: ctx.placeholder(m.group(0), "EMAIL"), text)
    text = rules.project_pattern.sub(lambda m: ctx.placeholder(m.group(0), "PROJ"), text)
    text = rules.org_pattern.sub(lambda m: ctx.placeholder(m.group(0), "ORG"), text)
    return text


def redact_messages(
    messages: Iterable[dict[str, object]],
    ctx: RedactionContext,
    *,
    rules: RedactionRules | None = None,
) -> list[dict[str, object]]:
    """对 LLM messages 列表的 ``content`` 字段应用脱敏,返回新列表。

    - 不修改入参（dict 浅拷贝）。
    - ``content`` 非字符串(如 LiteLLM 的 multi-part)直接透传,不脱敏。
    """
    rules = rules or load_rules()
    out: list[dict[str, object]] = []
    for msg in messages:
        copy = dict(msg)
        content = copy.get("content")
        if isinstance(content, str):
            copy["content"] = redact(content, ctx, rules=rules)
        out.append(copy)
    return out


__all__ = [
    "PLACEHOLDER_RE",
    "RedactionContext",
    "RedactionRules",
    "load_rules",
    "redact",
    "redact_messages",
    "reset_rules_cache",
]
