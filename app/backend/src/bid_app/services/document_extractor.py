"""文档抽取(markitdown 包装,§22 M0 Day2)。

支持 .docx / .doc / .md / .txt / .pdf 等(markitdown 内置 mammoth + pdfminer +
其他 plugin)。FR-1.4 限制上传类型。

接口:
- ``extract_file(path)``:同步函数,返回 sanitize 后的 markdown 字符串;
  markitdown 不支持的格式 raise ``DocumentExtractError``,调用方决定如何
  把错误回传(api/projects.py 上传端点会写到 Document.extract_error 字段)
- ``extract_for_project(project_id)``:async,从 DB 读 documents 表 + 抽取,
  返回 ``{tech_spec_md, scoring_md, template_md}``

⚠️ R-8 修复(devops + team-lead report):用户上传 markitdown 不支持的格式
(扫描 PDF / 加密 doc 等)时,旧实现走 ``except Exception: read_bytes().decode``
fallback,把 binary 直接 decode-replace,产物含 ``\\u0000`` (NUL) + 一堆
``\\ufffd``。这串脏文本进 LangGraph state → AsyncPostgresSaver 写
postgres JSON → ``UntranslatableCharacter: unsupported Unicode escape sequence``
炸,workflow checkpoint 全断。

修法:
1. 把所有抽取产物用 ``_sanitize_for_json`` 过一遍——剥 NUL 和 C0 控制字符
   (postgres JSON 拒收),但保留 \\n / \\r / \\t
2. markitdown ``UnsupportedFormatException`` / ``FileConversionException``
   显式抛 ``DocumentExtractError``,**不再静默走 bytes fallback**
3. ``extract_for_project`` 读已抽取的 markdown_path 时也 sanitize 一遍
   (历史脏数据自愈)

CLI ``run_local`` 走 ``extract_file``,不需要 DB。
"""
from __future__ import annotations

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


def extract_file(path: str | Path) -> str:
    """把单个文件转成 markdown 字符串(sanitize 后)。

    .md / .markdown / .txt 直读 utf-8 文本;其他类型走 markitdown。
    markitdown 抛 ``UnsupportedFormatException`` / ``FileConversionException``
    时本函数 raise ``DocumentExtractError``——**不再 silent fallback 到
    bytes.decode**(R-8 根因)。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"document not found: {p}")

    if p.suffix.lower() in _TEXT_KIND_EXT:
        return _sanitize_for_json(
            p.read_text(encoding="utf-8", errors="replace")
        )

    try:
        md = MarkItDown(enable_plugins=False)
        result = md.convert(str(p))
        text = getattr(result, "text_content", None)
        if text is None:
            text = getattr(result, "markdown", None)
        if text is None:
            raise DocumentExtractError(
                f"markitdown returned empty result for {p.name}"
            )
        return _sanitize_for_json(text)
    except UnsupportedFormatException as e:
        log.warning(
            "markitdown_unsupported_format",
            path=str(p),
            suffix=p.suffix.lower(),
            error=str(e),
        )
        raise DocumentExtractError(
            f"markitdown 不支持该格式 {p.suffix!r}(可能是扫描 PDF / "
            f"加密文档 / 损坏文件): {e}"
        ) from e
    except FileConversionException as e:
        log.warning("markitdown_file_conversion_failed", path=str(p), error=str(e))
        raise DocumentExtractError(
            f"markitdown 转换失败:{e}"
        ) from e
    except MarkItDownException as e:
        log.warning("markitdown_other_failure", path=str(p), error=str(e))
        raise DocumentExtractError(
            f"markitdown 失败:{type(e).__name__}: {e}"
        ) from e
    except DocumentExtractError:
        # 已经是我们自己的语义化异常,直接透传
        raise
    except Exception as e:
        # 真未预料异常(IO 等):log + 抛 DocumentExtractError,**不再** bytes
        # fallback——避免把 binary 当文本污染下游
        log.exception("markitdown_extract_unexpected_failure", path=str(p))
        raise DocumentExtractError(
            f"抽取失败:{type(e).__name__}: {e}"
        ) from e


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
