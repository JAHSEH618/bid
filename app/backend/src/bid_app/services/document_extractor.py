"""文档抽取(markitdown 包装,§22 M0 Day2)。

支持 .docx / .doc / .md / .txt / .pdf 等(markitdown 内置 mammoth + pdfminer +
其他 plugin)。FR-1.4 限制上传类型,本服务对 markitdown 不识别的也尝试用
``str.decode('utf-8', errors='replace')`` 兜底。

接口:
- ``extract_file(path)``:同步函数,返回 markdown 字符串
- ``extract_for_project(project_id)``:async,从 DB 读 documents 表 + 抽取,
  返回 ``{tech_spec_md, scoring_md, template_md}``;M1 落 Document 模型后才能跑

CLI ``run_local`` 走 ``extract_file``,不需要 DB。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from markitdown import MarkItDown

log = structlog.get_logger()

_TEXT_KIND_EXT = {".md", ".markdown", ".txt"}


def extract_file(path: str | Path) -> str:
    """把单个文件转成 markdown 字符串。

    .md / .markdown / .txt 直读 utf-8 文本(避免 markitdown 偶尔误把 md
    格式重排导致表格/代码块走样);其他类型走 markitdown。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"document not found: {p}")

    if p.suffix.lower() in _TEXT_KIND_EXT:
        return p.read_text(encoding="utf-8", errors="replace")

    try:
        md = MarkItDown(enable_plugins=False)
        result = md.convert(str(p))
        text = getattr(result, "text_content", None)
        if text is None:
            text = getattr(result, "markdown", None)
        if text is None:
            raise RuntimeError("markitdown returned empty result")
        return text
    except Exception:
        log.exception("markitdown_extract_failed", path=str(p))
        # 兜底:无脑读 bytes 转 str
        return p.read_bytes().decode("utf-8", errors="replace")


async def extract_for_project(project_id: int) -> dict[str, str]:
    """从 DB ``documents`` 表读 3 类文档(tech_spec / scoring / template)
    返回 ``{tech_spec_md, scoring_md, template_md}``,直接喂给 WorkflowState。

    数据来源:``Document.markdown_path`` 是 ``api/projects.py``
    上传端点用 markitdown 抽取后落盘的 ``{project_dir}/uploads/{kind}.md`` 路径。
    本函数直接读取该 .md 文本,不再二次跑 markitdown(避免重复抽取的 IO)。

    多份同 kind:取 ``id`` 最大的(最新上传)一份。
    """
    from sqlalchemy import select

    from ..db import session_factory
    from ..models import Document  # type: ignore[attr-defined]

    out: dict[str, str] = {
        "tech_spec_md": "",
        "scoring_md": "",
        "template_md": "",
    }
    kind_to_field = {
        "tech_spec": "tech_spec_md",
        "scoring": "scoring_md",
        "template": "template_md",
    }

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(Document)
                .where(Document.project_id == project_id)
                .order_by(Document.id.asc())
            )
        ).scalars().all()

    # 用 id ASC 遍历,后写覆盖前写,等价于"取最新上传(最大 id)"
    for doc in rows:
        kind = getattr(doc, "kind", None)
        if kind not in kind_to_field:
            continue
        md_path = getattr(doc, "markdown_path", None)
        if not md_path:
            log.warning(
                "doc_missing_markdown_path",
                project_id=project_id,
                kind=kind,
                doc_id=getattr(doc, "id", None),
            )
            continue
        try:
            out[kind_to_field[kind]] = Path(md_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            log.exception(
                "read_markdown_failed",
                project_id=project_id,
                kind=kind,
                path=md_path,
            )
    return out


def extract_files(
    *,
    tech_spec: str | Path,
    scoring: str | Path,
    template: str | Path,
) -> dict[str, str]:
    """CLI 友好版本:从 3 个本地文件路径直接抽取,不查 DB。"""
    return {
        "tech_spec_md": extract_file(tech_spec),
        "scoring_md": extract_file(scoring),
        "template_md": extract_file(template),
    }
