"""LangGraph 编译入口(§10.2)。

DAG 结构(11 节点 + END,与 §10.2 严格对齐)::

    extract_documents
        → generate_outline
        → parse_outline
        → outline_review (interrupt — D-K)
        → pick_chapter
        → chapter_generate_gate (interrupt — choose chapter model)
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

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

from .nodes import (
    assemble,
    categorize_blackboard,
    chapter_generate_gate,
    extract_documents,
    gen_visuals,
    generate_outline,
    human_review,
    material_understanding,
    material_understanding_review,
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


def _route_after_pick(state: WorkflowState) -> str:
    """PR-M9-1:pick_chapter 后,若 current_index 已超过 chapters 长度
    (常见于 selected 集合内章节全跑完,后面全是 skipped 的尾部),直接
    进 assemble,不再调下游 generate gate。"""
    chapters = state.get("chapters") or []
    if state.get("current_index", 0) >= len(chapters):
        return "assemble"
    return "chapter_generate_gate"


def _route_after_material_review(state: WorkflowState) -> str:
    """PR-M8-1 + Phase 1A:material_understanding_review 之后的分支。

    - revise → 回到 material_understanding 节点重新跑 LLM-0 (revision_feedback 已写)
    - pass / skip → 进 categorize_blackboard 拆 10 桶,再 generate_outline
      (用户在材料理解页 revise 时**不跑** categorize,节省 LLM 调用)
    """
    decision = state.get("_material_review_decision") or "pass"
    if decision == "revise":
        return "material_understanding"
    return "categorize_blackboard"


def _route_after_outline_review(state: WorkflowState) -> str:
    """textarea TOC + revise:outline_review 之后的分支。

    - revise → 回到 generate_outline 节点(``_outline_revision_feedback`` 已写)
    - confirm(默认)→ 进 pick_chapter 章节循环
    """
    decision = state.get("_outline_review_decision") or "confirm"
    if decision == "revise":
        return "generate_outline"
    return "pick_chapter"


def _route_after_merge(state: WorkflowState) -> str:
    """D-EI Stage 4:merge_chapter 之后的分支。

    - ``_should_auto_revise=True`` → 自动 revise:把 issues hint 作为
      ``revision_feedback`` 喂回 ``write_chapter`` 重写一次(retry_count
      未 ++ 在 update_state 里处理,这里走捷径直接回 write_chapter,
      在 ``_apply_auto_revise_hint`` 节点里 ++)
    - 否则(无 error / retry 额度用完) → 进 ``human_review``
    """
    return "apply_auto_revise" if state.get("_should_auto_revise") else "human_review"


async def _apply_auto_revise_hint(state: WorkflowState) -> dict[str, Any]:
    """D-EI Stage 4:把校验 issues hint 拼成 revision_feedback,bump retry_count。

    一个轻量级"伪节点":不调 LLM,只做 state 转换:
    - ``retry_count += 1``
    - ``revision_feedback = issues_to_hint(issues)``
    - 清空 ``_should_auto_revise`` 避免下一轮重复触发
    然后下游条件边回到 ``write_chapter``,LLM-2 据 revision_feedback 重写。
    """
    from ..services.template_validator import ValidationIssue, issues_to_hint

    raw_issues = state.get("_validation_issues") or []
    issues: list[ValidationIssue] = []
    if isinstance(raw_issues, list):
        for r in raw_issues:
            if isinstance(r, dict):
                issues.append(
                    ValidationIssue(
                        code=str(r.get("code", "")),
                        severity=str(r.get("severity", "warn")),
                        message=str(r.get("message", "")),
                        hint=str(r.get("hint", "")),
                    )
                )
    hint = issues_to_hint(issues) or "请按模版规范重写本章。"
    return {
        "retry_count": int(state.get("retry_count", 0) or 0) + 1,
        "revision_feedback": hint,
        "_should_auto_revise": False,
    }


def build_graph(checkpointer: AsyncPostgresSaver | None = None) -> Any:
    """编译 LangGraph workflow。``checkpointer`` 由 worker lifecycle 注入。

    M0 CLI ``run_local`` 时不带 checkpointer(用 ``None``,LangGraph 默认
    in-memory 路径,interrupt 在单进程内仍能 resume),M1+ 由 worker 注入
    AsyncPostgresSaver。

    返回 ``CompiledStateGraph`` 但 langgraph 0.6 没暴露稳定 type 给我们标
    注,这里返 ``Any``。worker.tasks 用 ``await graph.ainvoke(...)`` 不依赖
    具体方法签名。
    """
    g = StateGraph(WorkflowState)

    g.add_node("extract_documents", extract_documents.run)
    g.add_node("material_understanding", material_understanding.run)
    g.add_node(
        "material_understanding_review", material_understanding_review.run
    )
    g.add_node("categorize_blackboard", categorize_blackboard.run)
    g.add_node("generate_outline", generate_outline.run)
    g.add_node("parse_outline", parse_outline.run)
    g.add_node("outline_review", outline_review.run)  # ⭐ P4 interrupt(D-K)
    g.add_node("pick_chapter", pick_chapter.run)
    g.add_node("chapter_generate_gate", chapter_generate_gate.run)
    g.add_node("write_chapter", write_chapter.run)
    g.add_node("gen_visuals", gen_visuals.run)
    g.add_node("merge_chapter", merge_chapter.run)
    g.add_node("apply_auto_revise", _apply_auto_revise_hint)  # D-EI Stage 4
    g.add_node("human_review", human_review.run)  # ⭐ P5 interrupt(§10.6b)
    g.add_node("update_state", update_state.run)
    g.add_node("assemble", assemble.run)

    g.set_entry_point("extract_documents")
    g.add_edge("extract_documents", "material_understanding")
    g.add_edge("material_understanding", "material_understanding_review")
    # PR-M8-1 + Phase 1A:material_review 之后按 decision 分支。
    # pass / skip 走 categorize_blackboard;revise 回 material_understanding
    # (重读材料理解时不重跑实体桶,节省 LLM 调用)。
    g.add_conditional_edges(
        "material_understanding_review",
        _route_after_material_review,
        {
            "material_understanding": "material_understanding",
            "categorize_blackboard": "categorize_blackboard",
        },
    )
    g.add_edge("categorize_blackboard", "generate_outline")
    g.add_edge("generate_outline", "parse_outline")
    g.add_edge("parse_outline", "outline_review")
    # textarea TOC + revise:outline_review 之后按 decision 分支
    g.add_conditional_edges(
        "outline_review",
        _route_after_outline_review,
        {
            "generate_outline": "generate_outline",
            "pick_chapter": "pick_chapter",
        },
    )
    # PR-M9-1:若 pick_chapter 在 selected_chapter_ids 全部跑完后落到尾部
    # 的连续 unselected 章节,current_index 越界 → 直接进 assemble
    g.add_conditional_edges(
        "pick_chapter",
        _route_after_pick,
        {
            "chapter_generate_gate": "chapter_generate_gate",
            "assemble": "assemble",
        },
    )
    g.add_edge("chapter_generate_gate", "write_chapter")
    g.add_edge("write_chapter", "gen_visuals")
    g.add_edge("gen_visuals", "merge_chapter")
    # D-EI Stage 4:merge_chapter 之后跑校验,有 error 且 retry 额度未满
    # → apply_auto_revise → write_chapter 重试一次;否则进 human_review
    g.add_conditional_edges(
        "merge_chapter",
        _route_after_merge,
        {
            "apply_auto_revise": "apply_auto_revise",
            "human_review": "human_review",
        },
    )
    g.add_edge("apply_auto_revise", "write_chapter")
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
