"""LangGraph 编译入口(§10.2)。

DAG 结构::

    extract_documents
        → generate_outline
        → parse_outline
        → outline_review (interrupt)
        → pick_chapter
        → write_chapter
        → review_chapter (LLM-3 visuals)
        → merge_chapter (template merge + human review interrupt)
        → update_state
        → (current_index < total → pick_chapter | else → assemble)
        → END

⚠️ 不在本文件实例化 ``AsyncPostgresSaver`` —— 实例化交给 worker
``lifecycle.on_startup`` (§17.2),保证 LangGraph checkpoint 与 arq 进程绑定。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from .nodes import (
    assemble,
    extract_documents,
    generate_outline,
    merge_chapter,
    outline_review,
    parse_outline,
    pick_chapter,
    review_chapter,
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
    g.add_node("outline_review", outline_review.run)  # ⭐ P4 暂停点(D-K)
    g.add_node("pick_chapter", pick_chapter.run)
    g.add_node("write_chapter", write_chapter.run)
    g.add_node("review_chapter", review_chapter.run)  # LLM-3 可视化
    g.add_node("merge_chapter", merge_chapter.run)  # 合并 + P5 interrupt
    g.add_node("update_state", update_state.run)
    g.add_node("assemble", assemble.run)

    g.set_entry_point("extract_documents")
    g.add_edge("extract_documents", "generate_outline")
    g.add_edge("generate_outline", "parse_outline")
    g.add_edge("parse_outline", "outline_review")
    g.add_edge("outline_review", "pick_chapter")
    g.add_edge("pick_chapter", "write_chapter")
    g.add_edge("write_chapter", "review_chapter")
    g.add_edge("review_chapter", "merge_chapter")
    g.add_edge("merge_chapter", "update_state")
    g.add_conditional_edges(
        "update_state",
        _route_after_update,
        {"pick_chapter": "pick_chapter", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)

    if checkpointer is None:
        return g.compile()
    return g.compile(checkpointer=checkpointer)
