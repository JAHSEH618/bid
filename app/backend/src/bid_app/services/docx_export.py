"""DOCX 导出(M0 smoke 版,D-DZ)。

⚠️ 本文件**只是 M0 smoke**:Pandoc 直转,**不**预渲染 mermaid、**不**挂
``reference.docx``。设计稿 §13.1 的完整版(双层锁、mermaid 中文字体、
SLA、D-BD/D-BR/D-BN 等)留到 M3 (#20) 替换。

M0 验收口径(§22 / §23):``pandoc proposal.md -o smoke.docx`` 不报错,
Word 能打开;样式 / 中文 mermaid 不要求。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger()


async def export_docx_smoke(
    *,
    markdown: str,
    project_dir: Path,
    output_name: str = "proposal.smoke.docx",
) -> Path:
    """把 markdown 直转 docx(无 mermaid 渲染、无 reference.docx)。

    返回 docx 文件路径。
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    md_path = project_dir / "proposal.md"
    md_path.write_text(markdown, encoding="utf-8")

    out_path = project_dir / output_name

    proc = await asyncio.create_subprocess_exec(
        "pandoc",
        str(md_path),
        "-o",
        str(out_path),
        "--standalone",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {err.decode(errors='replace')}")
    return out_path


async def export_docx(
    *,
    markdown: str,
    project_dir: Path,
    project_name: str,
    reference_doc: Path | None = None,
    redis_url: str | None = None,
    on_stage: object | None = None,
    job_id: int | None = None,
) -> Path:
    """⚠️ M0 占位的"完整签名"版本 — 调用 ``export_docx_smoke`` 转 docx,
    其他参数(reference_doc / redis_url / on_stage / job_id)接受但忽略。

    M3 (#20) 用 §13.1 完整版替换:双层锁、mermaid 预渲染、reference-doc。
    """
    log.info(
        "docx_export_smoke",
        project_name=project_name,
        has_reference=reference_doc is not None and Path(reference_doc).exists()
        if reference_doc
        else False,
    )
    suffix = f".{job_id}" if job_id is not None else ""
    return await export_docx_smoke(
        markdown=markdown,
        project_dir=project_dir,
        output_name=f"proposal{suffix}.tmp.docx",
    )
