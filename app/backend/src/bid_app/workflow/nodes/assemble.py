"""全文整合 + 持久化输出(§10.6c / v10 §4.6)。

所有章节 finalized 后跑;同步:
- 写 ``{project_dir}/proposal.md``(给 docx 任务读)
- ``Run.finished_at`` + ``status='done'``
- ``Project.status='done'``
- SSE ``proposal_ready``

⭐ D-CG + D-CM:重写 proposal.md 后 DOCX 缓存必须失效。
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog

from ...db import session_factory
from ..prompts.assemble_prompt import assemble_proposal
from ..state import WorkflowState
from ..sync import publish_event, sync_project_status

log = structlog.get_logger()


async def run(state: WorkflowState) -> dict[str, Any]:
    pid = state["project_id"]
    run_id = state.get("run_id")

    final_md = assemble_proposal(
        list(state.get("finalized_chapters") or []),
        total_chapters=len(state.get("chapters") or []),
    )

    # 取 project_dir,Run 落 done
    project_dir: Path | None = None
    async with session_factory() as s:
        try:
            prj = await s.execute(
                sa.text("SELECT dir_path FROM projects WHERE id=:p"),
                {"p": pid},
            )
            project_dir = Path(prj.scalar_one())
        except Exception:
            log.warning("assemble_no_project_dir_lookup", project_id=pid)

        if run_id is not None:
            try:
                await s.execute(
                    sa.text(
                        "UPDATE runs SET finished_at=:t, status='done' "
                        "WHERE id=:r"
                    ),
                    {"r": run_id, "t": datetime.now(UTC)},
                )
                await s.commit()
            except Exception:
                log.exception("assemble_run_status_update_failed", run_id=run_id)

    # 写 proposal.md(给 generate_docx_task 读)
    if project_dir is not None:
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "proposal.md").write_text(final_md, encoding="utf-8")
        except Exception:
            log.exception(
                "assemble_proposal_md_write_failed", project_dir=str(project_dir)
            )

        # ⭐ D-CG + D-CM:重写 proposal.md 后 DOCX 缓存必须失效
        docx_path = project_dir / "proposal.docx"
        if docx_path.exists():
            try:
                docx_path.unlink()
            except Exception:
                log.exception(
                    "docx_invalidate_unlink_failed",
                    project_id=pid,
                    path=str(docx_path),
                )

    # D-CG/D-CM:把已存在或在跑的 DocxJob 都标 invalidated
    async with session_factory() as s:
        try:
            await s.execute(
                sa.text(
                    "UPDATE docx_jobs SET status='invalidated', "
                    "output_path=NULL, updated_at=NOW(), "
                    "error=COALESCE(error,'') || "
                    "  CASE WHEN COALESCE(error,'')='' THEN '' ELSE ' | ' END || "
                    "  'markdown invalidated by new assemble' "
                    "WHERE project_id=:p AND status IN "
                    "('done','pending','rendering_mermaid','pandoc','finalizing')"
                ),
                {"p": pid},
            )
            await s.commit()
        except Exception:
            log.warning(
                "assemble_docx_invalidate_skipped",
                project_id=pid,
                reason="docx_jobs table not yet migrated",
            )

    await sync_project_status(pid, "done")
    await publish_event(pid, "proposal_ready")

    return {"final_proposal": final_md}
