"""DOCX 导出 — D5 简化方案(§13.1 完整版)。

mermaid 预渲染 + pandoc 直转,全局串行(D-H):asyncio.Lock + Redis Lock 双层。

修复点:
- D-L:缓存路径**固定** ``proposal.docx``,文件名展示走下载端 FileResponse(filename)
- D-N:mermaid 用 ``re.finditer`` + 反向 span 替换,容忍 CRLF / 行尾空格
- D-H:Redis 锁用 ``redis.asyncio.Lock``(token + Lua CAS,自带阻塞等待)
- D-BD:``on_stage`` 回调让上层 task 在 mermaid 完毕、进入 pandoc 阶段时
  update DocxJob.status='pandoc'
- D-BN:写到 ``proposal.{job_id}.tmp.docx`` 而不是直接覆盖,task 在 done
  UPDATE 之后 atomic rename 成 ``proposal.docx``
- D-BR:``_module_lock`` 加 timeout=120s,防 DOCX 拥塞饿死 workflow task
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis_async
import structlog

log = structlog.get_logger()
_module_lock = asyncio.Lock()
_MODULE_LOCK_TIMEOUT = 120  # ⭐ D-BR:同进程锁等待上限,与 Redis blocking 一致
_REDIS_LOCK_KEY = "bid_app:lock:docx_export"
_REDIS_LOCK_TTL = 300  # 持锁 TTL,大于一次 docx 生成耗时
_REDIS_LOCK_BLOCKING_TIMEOUT = 120  # 等位最长 2 分钟超时(D-AA)

# ⭐ D-N:兼容 ``` 与 ~~~ 围栏,容忍语言名前后空格、行尾 \r、闭合 fence 行首空格
MERMAID_RE = re.compile(
    r"(?P<fence>```|~~~)[ \t]*mermaid[ \t]*\r?\n"
    r"(?P<code>.*?)\r?\n"
    r"(?P=fence)[ \t]*(?=\r?\n|$)",
    re.DOTALL,
)


OnStageCallable = Callable[[str], Awaitable[None]]


async def export_docx(
    *,
    markdown: str,
    project_dir: Path,
    project_name: str,
    reference_doc: Path | None = None,
    redis_url: str,
    on_stage: OnStageCallable | None = None,
    job_id: int | None = None,
) -> Path:
    """串行化包装,**返回临时 ``.tmp.docx`` 路径**;atomic rename 由调用方做。

    ⭐ D-BD:``on_stage`` 回调让上层在 mermaid 完毕、进入 pandoc 阶段时
    update DocxJob.status='pandoc';**不在这里 catch on_stage 异常**——
    on_stage 抛 ``_StaleJob`` 之类的信号必须透传到 task 顶层。
    """
    try:
        await asyncio.wait_for(_module_lock.acquire(), timeout=_MODULE_LOCK_TIMEOUT)
    except TimeoutError as te:
        raise TimeoutError(f"docx module lock timeout after {_MODULE_LOCK_TIMEOUT}s") from te
    try:
        async with _redis_lock(redis_url):
            return await _export_docx_inner(
                markdown,
                project_dir,
                reference_doc,
                on_stage=on_stage,
                job_id=job_id,
            )
    finally:
        _module_lock.release()
    # 防 lint 把 project_name 标 unused
    _ = project_name


@asynccontextmanager
async def _redis_lock(redis_url: str) -> AsyncIterator[None]:
    """正确的 Redis 互斥锁:用 ``redis.asyncio.Lock``(token + Lua CAS)。"""
    r = redis_async.from_url(redis_url)
    lock = r.lock(
        _REDIS_LOCK_KEY,
        timeout=_REDIS_LOCK_TTL,
        blocking=True,
        blocking_timeout=_REDIS_LOCK_BLOCKING_TIMEOUT,
        thread_local=False,
    )
    acquired = await lock.acquire()
    if not acquired:
        await r.aclose()
        raise TimeoutError(f"docx export lock timeout after {_REDIS_LOCK_BLOCKING_TIMEOUT}s")
    try:
        yield
    finally:
        try:
            await lock.release()
        except Exception:
            log.exception("redis_lock_release_failed")
        await r.aclose()


async def _export_docx_inner(
    markdown: str,
    project_dir: Path,
    reference_doc: Path | None,
    *,
    on_stage: OnStageCallable | None = None,
    job_id: int | None = None,
) -> Path:
    work = project_dir / "docx-build"
    work.mkdir(parents=True, exist_ok=True)

    # 1. mermaid 预渲染(图片用相对路径,后面 pandoc 用 --resource-path 解析)
    inlined = await _render_mermaid(markdown, work)

    md_path = work / "proposal_inlined.md"
    md_path.write_text(inlined, encoding="utf-8")

    # ⭐ D-BN:写临时文件;调用方在 DB done 成功后做 atomic rename
    suffix = f".{job_id}" if job_id is not None else ""
    out_path = project_dir / f"proposal{suffix}.tmp.docx"

    # ⭐ D-BD + D-CB:mermaid 完毕,通知上层切 status='pandoc';**不 catch 异常**——
    # on_stage 抛 _StaleJob (D-BX) 必须透传到 task 顶层
    if on_stage is not None:
        await on_stage("pandoc")

    # 2. pandoc 直转
    args = [
        "pandoc",
        str(md_path),
        "-o",
        str(out_path),
        "--resource-path",
        str(work),  # 让相对图片路径(./mmd_0.png)能解析
        "--standalone",
    ]
    if reference_doc is not None and Path(reference_doc).exists():
        args.append(f"--reference-doc={reference_doc}")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {err.decode(errors='replace')}")

    return out_path


async def _render_mermaid(markdown: str, work: Path) -> str:
    """逐个 mermaid 块渲染 PNG,markdown 中替换为图片引用。

    ⭐ D-N:``re.finditer`` 找出所有 span,**反向**替换(从后往前),
    防止前一个替换改变后一个 span 的偏移。失败的块保留原 fence(降级容错)。
    """
    matches = list(MERMAID_RE.finditer(markdown))
    if not matches:
        return markdown
    if shutil.which("mmdc") is None:
        log.warning(
            "mermaid_cli_not_installed",
            blocks=len(matches),
            hint="keeping mermaid fences in DOCX markdown",
        )
        return markdown

    rendered: list[Path | None] = []
    for i, m in enumerate(matches):
        code = m.group("code")
        src = work / f"mmd_{i}.mmd"
        png = work / f"mmd_{i}.png"
        src.write_text(code, encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                "mmdc",
                "-i",
                str(src),
                "-o",
                str(png),
                "-b",
                "transparent",
                "-c",
                "/etc/mermaid-config.json",
                "-p",
                "/etc/puppeteer-config.json",
                "--cssFile",
                "/etc/mermaid.css",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            log.warning(
                "mermaid_cli_not_found_during_render",
                blocks=len(matches),
                hint="keeping remaining mermaid fences",
            )
            return markdown
        _, err = await proc.communicate()
        if proc.returncode == 0 and png.exists():
            rendered.append(png)
        else:
            log.warning(
                "mermaid_render_failed",
                index=i,
                error=err.decode(errors="replace"),
            )
            rendered.append(None)

    # 反向替换(从最后一个 match 起,改原文不影响前面 match.start/end)
    out = markdown
    for m, png_or_none in zip(reversed(matches), reversed(rendered), strict=True):
        if png_or_none is None:
            continue  # 保留原 fence 块,降级容错
        replacement = f"![]({png_or_none.name})"
        out = out[: m.start()] + replacement + out[m.end() :]

    return out


def sanitize_filename(name: str) -> str:
    """文件名安全化(给下载端展示用,与 ``_export_docx_inner`` 解耦)。"""
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name)[:80] or "proposal"


# ===== M0 smoke 兼容(保留旧入口,给 cli/run_local 用) =====


async def export_docx_smoke(
    *,
    markdown: str,
    project_dir: Path,
    output_name: str = "proposal.smoke.docx",
) -> Path:
    """M0 smoke:仅 Pandoc 直转,**不**预渲染 mermaid、**不**挂 reference.docx。

    保留给 ``cli/run_local`` 用(M0 验收口径不要求 mermaid 中文 / reference)。
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
