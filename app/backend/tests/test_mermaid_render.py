"""D-EN: Mermaid → PNG 渲染 + 属性 + 失败回退测试。

只测试 ``_render_mermaid`` 的字符串处理逻辑;真实 mmdc 渲染由
docker 容器内集成,本地无 mmdc 时走 fallback 路径。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bid_app.services.docx_export import MERMAID_RE, _render_mermaid


@pytest.mark.asyncio
async def test_render_mermaid_no_blocks_passthrough(tmp_path: Path) -> None:
    """正文无 mermaid → 原样返回。"""
    md = "# 普通文档\n\n没有图表。"
    out = await _render_mermaid(md, tmp_path)
    assert out == md


@pytest.mark.asyncio
async def test_render_mermaid_no_mmdc_keeps_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mmdc 不存在 → 保留原 fence,工作流不阻塞。"""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    md = "## 流程\n\n```mermaid\nflowchart TD\nA-->B\n```\n"
    out = await _render_mermaid(md, tmp_path)
    # 没换图,fence 还在
    assert "```mermaid" in out
    assert out == md


@pytest.mark.asyncio
async def test_render_mermaid_attributes_60pct_center(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mmdc 成功 → 替换为 image,带 width=60% + fig-align=center。"""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/mmdc")

    class _StubProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*args: object, **_kw: object) -> _StubProc:
        # 模拟 mmdc 真的写出 PNG:找到 -o 后面的路径,touch 一下
        a = list(args)
        try:
            o_idx = a.index("-o")
            png_path = Path(a[o_idx + 1])
            png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        except (ValueError, IndexError):
            pass
        return _StubProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    md = "## 流程\n\n```mermaid\nflowchart TD\nA-->B\n```\n"
    out = await _render_mermaid(md, tmp_path)
    assert "width=60%" in out
    assert "fig-align=center" in out
    assert "```mermaid" not in out  # fence 已被替换
    assert ".png)" in out


@pytest.mark.asyncio
async def test_render_mermaid_failure_keeps_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mmdc 退出码非零 → 保留对应 fence,其他成功的块仍替换。"""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/mmdc")

    class _FailProc:
        returncode = 2

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"mermaid syntax error")

    async def _fake_exec(*_a: object, **_kw: object) -> _FailProc:
        return _FailProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    md = "## 流程\n\n```mermaid\nflowchart TD\nA-->B\n```\n"
    out = await _render_mermaid(md, tmp_path)
    # 失败 → 原 fence 保留
    assert "```mermaid" in out
    assert "width=60%" not in out


def test_mermaid_regex_matches_backtick_fence() -> None:
    md = "```mermaid\nflowchart TD\nA-->B\n```\n"
    matches = list(MERMAID_RE.finditer(md))
    assert len(matches) == 1
    assert "flowchart" in matches[0].group("code")


def test_mermaid_regex_matches_tilde_fence() -> None:
    md = "~~~mermaid\nflowchart LR\nX-->Y\n~~~\n"
    matches = list(MERMAID_RE.finditer(md))
    assert len(matches) == 1


def test_mermaid_regex_skips_non_mermaid_blocks() -> None:
    md = "```python\nprint('hi')\n```\n"
    matches = list(MERMAID_RE.finditer(md))
    assert matches == []
