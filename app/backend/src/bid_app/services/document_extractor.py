"""文档抽取(markitdown 包装,§22 M0 Day2)。

支持 .docx / .doc / .md / .txt / .pdf 等(markitdown 内置 mammoth + pdfminer +
其他 plugin;.doc 老 OLE 格式走 LibreOffice headless 转 .docx 后再过 markitdown,
见 R-9)。FR-1.4 限制上传类型。

接口:
- ``extract_file(path)``:同步函数,返回 sanitize 后的 markdown 字符串;
  markitdown 不支持的格式 raise ``DocumentExtractError``,调用方决定如何
  把错误回传(api/projects.py 上传端点会写到 Document.extract_error 字段)
- ``extract_for_project(project_id)``:async,从 DB 读 documents 表 + 抽取,
  返回 ``{tech_spec_md, scoring_md, template_md}``

⚠️ R-8 修复:用户上传 markitdown 不支持的格式时旧实现 silent
``read_bytes().decode-replace`` fallback 把 NUL/C0 binary 喂进 LangGraph state
→ postgres JSON 拒收 → checkpoint 全断。修:`_sanitize_for_json` 单一信源
+ ``DocumentExtractError`` 显式抛 + 读已抽取产物时也 sanitize(自愈)。

⚠️ R-9 修复:.doc 老 OLE 格式 markitdown 内置 DocxConverter 不支持
(只支持 .docx OOXML)。先用 ``libreoffice --headless --convert-to docx``
转成 .docx 再过 markitdown(完整保留表格/标题层级)。LibreOffice 由
Dockerfile ``libreoffice-core libreoffice-writer`` 提供;本地 dev 没装
则 raise ``DocumentExtractError`` 提示装包或换 .docx。

CLI ``run_local`` 走 ``extract_file``,不需要 DB。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog
from markitdown import (
    FileConversionException,
    MarkItDown,
    MarkItDownException,
    UnsupportedFormatException,
)

log = structlog.get_logger()

_TEXT_KIND_EXT = {".md", ".markdown", ".txt"}
_LEGACY_DOC_EXT = {".doc"}  # 老 OLE Word,需 LibreOffice headless 转 .docx
_LIBREOFFICE_CONVERT_TIMEOUT_SECONDS = 60


class DocumentExtractError(Exception):
    """markitdown 不支持或彻底失败时显式抛(替换原 silent bytes fallback)。

    api/projects.py 上传端点 catch 后写 ``Document.extract_error`` 字段,
    workflow 启动后 ``extract_for_project`` 读到空文本(对应 doc kind 字段
    保持 ""),LLM 仍能继续(只是缺少该份资料);用户从 UI 看到错误。
    """


# postgres JSON 拒 C0 控制字符(0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F),
# 保留 \t (0x09) / \n (0x0A) / \r (0x0D)。
_FORBIDDEN_CONTROLS = frozenset(
    chr(c)
    for c in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20))
)


def _sanitize_for_json(text: str) -> str:
    """剥掉 postgres JSON 拒收的 C0 控制字符 + NUL,保留可见字符 + 换行 / tab。

    R-8 防御:LangGraph checkpoint 走 postgres JSON 列存,任何含 NUL 的字符串
    都会触发 ``psycopg.errors.UntranslatableCharacter``。本函数是单一信源,
    所有 ``extract_*`` 路径都过它,workflow state 写入前不会再有脏字符。
    """
    if not text:
        return ""
    # 显式剥常见 NUL(快路径),再走集合过滤兜底
    if "\x00" in text:
        text = text.replace("\x00", "")
    if any(c in _FORBIDDEN_CONTROLS for c in text[:256]):
        # 只在前 256 字节嗅探到才走慢路径(典型情况空 / 干净 markitdown 输出)
        text = "".join(c for c in text if c not in _FORBIDDEN_CONTROLS)
    return text


def _convert_doc_to_docx(doc_path: Path) -> Path:
    """R-9:用 LibreOffice headless 把 .doc(老 OLE)转 .docx(OOXML)。

    返回新生成的 .docx 路径(在临时目录里,调用方负责清理或不管,
    随 tempdir 自动清)。调用方应在 ``with tempfile.TemporaryDirectory()``
    上下文里调本函数,避免临时文件泄漏。

    LibreOffice 不在 PATH(本地 dev / Dockerfile 没装)→ raise
    ``DocumentExtractError`` 提示运维 / 用户。
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise DocumentExtractError(
            ".doc 格式需要 LibreOffice 转换,但容器内未安装 "
            "(请安装 libreoffice-core + libreoffice-writer,或上传 .docx)"
        )

    out_dir = doc_path.parent  # 转出文件直接落在 doc 同目录(临时目录)
    args = [
        soffice,
        "--headless",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_dir),
        str(doc_path),
    ]
    log.info("libreoffice_convert_doc_start", path=str(doc_path))
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=_LIBREOFFICE_CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DocumentExtractError(
            f"LibreOffice 转换 .doc 超时 (>{_LIBREOFFICE_CONVERT_TIMEOUT_SECONDS}s)"
        ) from e

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
        raise DocumentExtractError(
            f"LibreOffice 转换 .doc 失败 (rc={proc.returncode}): {stderr}"
        )

    # LibreOffice 生成 ``<doc_stem>.docx``(同名换扩展)
    docx_path = out_dir / f"{doc_path.stem}.docx"
    if not docx_path.is_file():
        raise DocumentExtractError(
            f"LibreOffice 转换后未找到产物 {docx_path.name},stderr: "
            + proc.stderr.decode("utf-8", errors="replace")[:300]
        )
    log.info(
        "libreoffice_convert_doc_done",
        src=str(doc_path),
        dst=str(docx_path),
        size=docx_path.stat().st_size,
    )
    return docx_path


def _markitdown_convert(path: Path) -> str:
    """跑 markitdown,返回 sanitize 后的 markdown。失败统一抛
    ``DocumentExtractError``。"""
    try:
        md = MarkItDown(enable_plugins=False)
        result = md.convert(str(path))
        text = getattr(result, "text_content", None)
        if text is None:
            text = getattr(result, "markdown", None)
        if text is None:
            raise DocumentExtractError(
                f"markitdown returned empty result for {path.name}"
            )
        return _sanitize_for_json(text)
    except UnsupportedFormatException as e:
        log.warning(
            "markitdown_unsupported_format",
            path=str(path),
            suffix=path.suffix.lower(),
            error=str(e),
        )
        raise DocumentExtractError(
            f"markitdown 不支持该格式 {path.suffix!r}(可能是扫描 PDF / "
            f"加密文档 / 损坏文件): {e}"
        ) from e
    except FileConversionException as e:
        log.warning("markitdown_file_conversion_failed", path=str(path), error=str(e))
        raise DocumentExtractError(f"markitdown 转换失败:{e}") from e
    except MarkItDownException as e:
        log.warning("markitdown_other_failure", path=str(path), error=str(e))
        raise DocumentExtractError(
            f"markitdown 失败:{type(e).__name__}: {e}"
        ) from e
    except DocumentExtractError:
        raise
    except Exception as e:
        log.exception("markitdown_extract_unexpected_failure", path=str(path))
        raise DocumentExtractError(
            f"抽取失败:{type(e).__name__}: {e}"
        ) from e


def extract_file(path: str | Path) -> str:
    """把单个文件转成 markdown 字符串(sanitize 后)。

    .md / .markdown / .txt 直读 utf-8 文本。
    .doc(老 OLE)先用 LibreOffice headless 转 .docx 再过 markitdown(R-9)。
    其他格式直接走 markitdown。

    markitdown 抛 ``UnsupportedFormatException`` / ``FileConversionException``
    时 raise ``DocumentExtractError``——**不再 silent fallback 到 bytes.decode**
    (R-8 根因)。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"document not found: {p}")

    if p.suffix.lower() in _TEXT_KIND_EXT:
        return _sanitize_for_json(
            p.read_text(encoding="utf-8", errors="replace")
        )

    # ⭐ R-9:.doc 老 OLE → 先 LibreOffice 转 .docx 再 markitdown
    if p.suffix.lower() in _LEGACY_DOC_EXT:
        with tempfile.TemporaryDirectory(prefix="bid_doc_convert_") as tmpdir:
            tmp_doc = Path(tmpdir) / p.name
            shutil.copyfile(p, tmp_doc)  # LibreOffice 生成 docx 落同目录
            docx_path = _convert_doc_to_docx(tmp_doc)
            return _markitdown_convert(docx_path)

    return _markitdown_convert(p)


async def extract_for_project(project_id: int) -> dict[str, str]:
    """从 DB ``documents`` 表读 3 类文档(tech_spec / scoring / template)
    返回 ``{tech_spec_md, scoring_md, template_md}``,直接喂给 WorkflowState。

    数据来源:``Document.markdown_path`` 是 ``api/projects.py``
    上传端点用 markitdown 抽取后落盘的 ``{project_dir}/uploads/{kind}.md`` 路径。

    多份同 kind:取 ``id`` 最大的(最新上传)一份。
    R-8 防御:读到的文本走 ``_sanitize_for_json`` 过滤,自愈历史脏数据。
    """
    from sqlalchemy import select

    from ..db import session_factory
    from ..models import Document

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
            raw = Path(md_path).read_text(encoding="utf-8", errors="replace")
            out[kind_to_field[kind]] = _sanitize_for_json(raw)
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
