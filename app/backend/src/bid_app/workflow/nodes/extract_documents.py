"""文档抽取节点(graph 入口节点)。

设计:绝大多数路径下,worker ``start_workflow_task`` 已用 ``build_initial_state``
把 ``tech_spec_md`` / ``scoring_md`` / ``template_md`` 装好了 state(M1+);M0
CLI ``run_local`` 也会自填这三个字段。本节点只做 sanity 兜底:
  - 若三字段都已存在,纯 pass-through(返回空 dict 让 LangGraph 不更新 state)
  - 若缺字段,fallback 调 ``services.document_extractor`` 重新读 DB(M1+ 才能跑)

返回:dict(LangGraph 节点签名),通常为空。
"""
from __future__ import annotations

import structlog

from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, str]:
    project_id = state.get("project_id")
    have_all = all(state.get(k) for k in ("tech_spec_md", "scoring_md", "template_md"))

    if have_all:
        if project_id is not None:
            await publish_event(project_id, "extract_documents_passthrough")
        return {}

    # Fallback:从 DB 读项目文档并 markitdown 抽取(M1+ services/document_extractor 落地后才会跑)
    try:
        from ...services.document_extractor import (  # type: ignore[attr-defined]
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
    if project_id is not None:
        await publish_event(project_id, "extract_documents_done")
    return extracted
