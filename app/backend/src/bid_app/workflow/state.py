"""LangGraph WorkflowState(§10.1)。

⚠️ **不放 ``api_key``**(D-C):防止被 PostgresSaver 落库。
运行时通过 ``project_id`` → ``Project.encrypted_api_key_snapshot`` → AES-GCM 解密。
"""
from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    # === 输入(只读)===
    project_id: int  # ⭐ DB 查询入口
    run_id: int
    tech_spec_md: str
    scoring_md: str
    template_md: str
    pages_per_chapter: int
    max_retry_per_chapter: int

    # === v10 §3.3 五个 Loop 变量(命名严格对齐设计稿)===
    chapters: list[dict[str, Any]]
    current_index: int
    retry_count: int
    finalized_chapters: list[str]
    revision_feedback: str

    # === Human Review 临时载体(由 Command(resume=...) 注入)===
    _review_decision: str  # approve | revise | skip
    _review_feedback: str

    # === Outline 编辑临时载体(P4 提纲确认,D-K)===
    # 由 /confirm-outline 端点通过 Command(resume={...}) 注入。
    # 若为 None / [] 走"自动确认",直接用 LLM-1 生成的 chapters 进入循环。
    _outline_confirmed_chapters: list[dict[str, Any]] | None

    # === 节点之间的临时载体 ===
    # generate_outline 输出 LLM-1 原始 JSON 字符串,parse_outline 消费。
    _outline_json: str
    # write_chapter 输出的章节正文,review_chapter (LLM-3 视觉)/merge_chapter 消费;
    # update_state 写完成后清空。
    _pending_chapter_text: str
    _pending_visuals_json: str
    # === 输出 ===
    final_proposal: str | None
