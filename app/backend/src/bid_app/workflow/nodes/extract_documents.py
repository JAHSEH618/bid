"""文档抽取节点(graph 入口节点)。

设计:绝大多数路径下,worker ``start_workflow_task`` 已用 ``build_initial_state``
把 ``tech_spec_md`` / ``scoring_md`` / ``template_md`` 装好了 state(M1+);M0
CLI ``run_local`` 也会自填这三个字段。本节点只做 sanity 兜底:
  - 若三字段都已存在,纯 pass-through(返回空 dict 让 LangGraph 不更新 state)
  - 若缺字段,fallback 调 ``services.document_extractor`` 重新读 DB(M1+ 才能跑)

PR-M7-3 / D2 新增:抽取完成后聚合所有 ``Document.structured_html`` →
``markdown_to_safe_html`` 清洗 → ``write_blackboard`` 原子写盘 + DB,
并把 ``blackboard_excerpt`` 注入 state 供下游 prompt 引用。

返回:dict(LangGraph 节点签名),通常含 blackboard_excerpt。
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog

from ...db import session_factory
from ...services.html_sanitize import markdown_to_safe_html
from ..blackboard import write_blackboard
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()

# context 上限 — 黑板长度 (字符) 截断,防止把太长的内容塞进每个 prompt
_BLACKBOARD_EXCERPT_LIMIT = 20_000


async def run(state: WorkflowState) -> dict[str, str]:
    project_id = state.get("project_id")
    have_all = all(state.get(k) for k in ("tech_spec_md", "scoring_md", "template_md"))

    extracted: dict[str, str] = {}
    if have_all:
        if project_id is not None:
            await publish_event(project_id, "extract_documents_passthrough")
    else:
        # Fallback:从 DB 读项目文档并 markitdown 抽取(M1+ services/document_extractor 落地后才会跑)
        try:
            from ...services.document_extractor import (
                extract_for_project,
            )
        except Exception:
            log.warning(
                "extract_documents_fallback_unavailable",
                project_id=project_id,
                reason="services.document_extractor not yet implemented",
            )
            return {}

        if project_id is None:
            log.warning("extract_documents_skipped_no_project_id")
            return {}

        extracted = await extract_for_project(project_id)
        await publish_event(project_id, "extract_documents_done")

    # ⭐ PR-M7-3 / D2:聚合 Document.structured_html → 清洗 HTML → 写黑板
    if project_id is not None:
        try:
            blackboard_excerpt = await _build_and_write_blackboard(project_id)
            extracted["blackboard_excerpt"] = blackboard_excerpt
        except Exception:
            # 黑板失败不阻塞主流程;extracted 已经够 LLM-1 起跑
            log.exception(
                "blackboard_write_failed_non_fatal",
                project_id=project_id,
            )

    return extracted


async def _build_and_write_blackboard(project_id: int) -> str:
    """聚合所有 ``Document.structured_html`` → 清洗 HTML → atomic 写黑板。

    返回 blackboard_excerpt 给下游 prompt 注入 (state.blackboard_excerpt)。
    超长截断到 ``_BLACKBOARD_EXCERPT_LIMIT`` 字符,保留最相关段 (按 id ASC,
    较早上传的文档优先)。
    """
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT id, original_filename, kind, tags, structured_html "
                    "FROM documents WHERE project_id=:p ORDER BY id ASC"
                ),
                {"p": project_id},
            )
        ).mappings().all()

    if not rows:
        return ""

    sections: list[str] = []
    for row in rows:
        md = row["structured_html"] or ""
        if not md.strip():
            continue
        header_bits: list[str] = [row["original_filename"] or "(unnamed)"]
        if row["kind"]:
            header_bits.append(f"kind={row['kind']}")
        if row["tags"]:
            header_bits.append("tags=" + ",".join(row["tags"]))
        header = " · ".join(header_bits)
        html = markdown_to_safe_html(md)
        sections.append(f"<h2>{header}</h2>\n{html}")

    blackboard_html = "\n\n".join(sections)
    await write_blackboard(project_id, blackboard_html)

    if len(blackboard_html) > _BLACKBOARD_EXCERPT_LIMIT:
        return blackboard_html[:_BLACKBOARD_EXCERPT_LIMIT] + "\n<!-- truncated -->"
    return blackboard_html
