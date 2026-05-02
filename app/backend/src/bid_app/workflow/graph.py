"""LangGraph 编译入口(§10.2)。

DAG 结构(11 节点 + END,与 §10.2 严格对齐)::

    extract_documents
        → generate_outline
        → parse_outline
        → outline_review (interrupt — D-K)
        → pick_chapter
        → write_chapter (LLM-2)
        → gen_visuals (LLM-3)
        → merge_chapter (template only)
        → human_review (interrupt — §10.6b)
        → update_state (state machine)
        → (current_index < total → pick_chapter | else → assemble)
        → END

⚠️ 不在本文件实例化 ``AsyncPostgresSaver`` —— 实例化交给 worker
``lifecycle.on_startup`` (§17.2),保证 LangGraph checkpoint 与 arq 进程绑定。

⚠️ **D-EE 决策记录**(M1-6 #8):M0-4 任务清单 (#1) 把 ``gen_visuals`` /
``merge_chapter`` / ``human_review`` 三节点压缩成 ``review_chapter`` +
``merge_chapter``(后者承担 template merge + interrupt)。M1-6 在评估 #37
ACCEPTANCE_AUDIT deviation 时按 §10.2 spec 拆回**严格 11 节点**:
  · ``review_chapter.py`` 重命名为 ``gen_visuals.py``(LLM-3 可视化)
  · ``merge_chapter.py`` 仅做模板转换(无 interrupt)
  · 新增 ``human_review.py`` 单独承担 P5 interrupt
理由:checkpointer 在节点之间提交 state,三节点拆分给"模板转换完成 / 等
人工审核 / 已 resume"提供独立的 checkpoint 边界,worker SIGKILL 后 resume
能精确回到正确阶段;同时与 §10.2 graph 描述、REVIEW-1 审查口径对齐。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from .nodes import (
    assemble,
    extract_documents,
    gen_visuals,
    generate_outline,
    human_review,
    merge_chapter,
    outline_review,
    parse_outline,
    pick_chapter,
    update_state,
    write_chapter,
)
from .state import WorkflowState

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def _route_after_update(state: WorkflowState) -> str:
    """更新后选择:还有章节就回 pick_chapter 否则去 assemble。"""
    chapters = state.get("chapters") or []
    return (
        "pick_chapter"
        if state.get("current_index", 0) < len(chapters)
        else "assemble"
    )


def build_graph(checkpointer: "AsyncPostgresSaver | None" = None):
    """编译 LangGraph workflow。``checkpointer`` 由 worker lifecycle 注入。

    M0 CLI ``run_local`` 时不带 checkpointer(用 ``None``,LangGraph 默认
    in-memory 路径,interrupt 在单进程内仍能 resume),M1+ 由 worker 注入
    AsyncPostgresSaver。
    """
    g: StateGraph = StateGraph(WorkflowState)

    g.add_node("extract_documents", extract_documents.run)
    g.add_node("generate_outline", generate_outline.run)
    g.add_node("parse_outline", parse_outline.run)
    g.add_node("outline_review", outline_review.run)  # ⭐ P4 interrupt(D-K)
    g.add_node("pick_chapter", pick_chapter.run)
    g.add_node("write_chapter", write_chapter.run)
    g.add_node("gen_visuals", gen_visuals.run)
    g.add_node("merge_chapter", merge_chapter.run)
    g.add_node("human_review", human_review.run)  # ⭐ P5 interrupt(§10.6b)
    g.add_node("update_state", update_state.run)
    g.add_node("assemble", assemble.run)

    g.set_entry_point("extract_documents")
    g.add_edge("extract_documents", "generate_outline")
    g.add_edge("generate_outline", "parse_outline")
    g.add_edge("parse_outline", "outline_review")
    g.add_edge("outline_review", "pick_chapter")
    g.add_edge("pick_chapter", "write_chapter")
    g.add_edge("write_chapter", "gen_visuals")
    g.add_edge("gen_visuals", "merge_chapter")
    g.add_edge("merge_chapter", "human_review")
    g.add_edge("human_review", "update_state")
    g.add_conditional_edges(
        "update_state",
        _route_after_update,
        {"pick_chapter": "pick_chapter", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)

    if checkpointer is None:
        return g.compile()
    return g.compile(checkpointer=checkpointer)
