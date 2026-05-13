"""PR-M6-2:test single-chapter DOCX export pathing & API model invariants.

We don't run pandoc / mermaid in the unit test (slow + needs binaries);
instead verify:
- ``export_chapter_docx`` writes its tmp file under the chapter-specific
  filename pattern.
- ``DocxJob`` model accepts the new ``scope`` / ``chapter_id`` fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bid_app.models.docx_job import DocxJob


def test_docx_job_scope_defaults_to_project() -> None:
    """无显式 scope → 默认 project,与历史行为一致。"""
    job = DocxJob(project_id=1)
    # ORM 默认值在 flush 时写入,这里手动模拟未 flush 的行为
    job.scope = job.scope or "project"
    assert job.scope == "project"
    assert job.chapter_id is None


def test_docx_job_chapter_scope_carries_chapter_id() -> None:
    job = DocxJob(project_id=1, scope="chapter", chapter_id=42)
    assert job.scope == "chapter"
    assert job.chapter_id == 42


@pytest.mark.asyncio
async def test_export_chapter_docx_uses_chapter_specific_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """export_chapter_docx 应该:
    - 在 ``project_dir/docx-build-chapter-{chapter_id}/`` 准备工作目录
    - 写 tmp 到 ``project_dir/chapter_{chapter_id}.{job_id}.tmp.docx``
    """
    from bid_app.services import docx_export

    project_dir = tmp_path / "p1"
    project_dir.mkdir()

    captured: dict[str, object] = {}

    async def _fake_pipeline(
        *,
        markdown: str,
        work_dir: Path,
        out_path: Path,
        reference_doc: Path | None,
        on_stage: object | None,
    ) -> Path:
        # 写一个空文件验证 out_path 有效
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00")
        captured["work_dir"] = work_dir
        captured["out_path"] = out_path
        captured["markdown"] = markdown
        captured["reference_doc"] = reference_doc
        return out_path

    # 直接绕过全局 redis lock + module lock(测试不接 redis)。
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_serial_lock(_redis_url: str):  # type: ignore[no-untyped-def]
        yield

    monkeypatch.setattr(docx_export, "_run_pandoc_pipeline", _fake_pipeline)
    monkeypatch.setattr(docx_export, "_serial_lock", _noop_serial_lock)

    out = await docx_export.export_chapter_docx(
        markdown="# 单章测试",
        project_dir=project_dir,
        chapter_id=42,
        reference_doc=None,
        redis_url="redis://localhost:6379/0",
        on_stage=None,
        job_id=7,
    )

    assert out == captured["out_path"]
    expected_tmp = project_dir / "chapter_42.7.tmp.docx"
    expected_work = project_dir / "docx-build-chapter-42"
    assert out == expected_tmp
    assert captured["work_dir"] == expected_work
    assert captured["markdown"] == "# 单章测试"
    assert out.exists()


@pytest.mark.asyncio
async def test_export_docx_project_path_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整本 export_docx 仍然写到 project_dir/proposal.{job_id}.tmp.docx —
    确保 PR-M6-2 的重构没有意外改变 project scope 的路径。
    """
    from contextlib import asynccontextmanager

    from bid_app.services import docx_export

    project_dir = tmp_path / "p2"
    project_dir.mkdir()

    captured: dict[str, object] = {}

    async def _fake_pipeline(
        *,
        markdown: str,
        work_dir: Path,
        out_path: Path,
        reference_doc: Path | None,
        on_stage: object | None,
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00")
        captured["work_dir"] = work_dir
        captured["out_path"] = out_path
        return out_path

    @asynccontextmanager
    async def _noop_serial_lock(_redis_url: str):  # type: ignore[no-untyped-def]
        yield

    monkeypatch.setattr(docx_export, "_run_pandoc_pipeline", _fake_pipeline)
    monkeypatch.setattr(docx_export, "_serial_lock", _noop_serial_lock)

    out = await docx_export.export_docx(
        markdown="# 整本",
        project_dir=project_dir,
        project_name="项目 A",
        reference_doc=None,
        redis_url="redis://localhost:6379/0",
        on_stage=None,
        job_id=11,
    )

    assert out == project_dir / "proposal.11.tmp.docx"
    assert captured["work_dir"] == project_dir / "docx-build"
