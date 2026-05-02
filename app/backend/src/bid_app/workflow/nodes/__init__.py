"""LangGraph 节点(M0 §10.2 + v10 设计稿)。

10 个节点 + 1 个 graph 文件:
  extract_documents → generate_outline → parse_outline → outline_review (interrupt)
    → pick_chapter → write_chapter → review_chapter (LLM-3 visuals)
    → merge_chapter (template merge + human review interrupt)
    → update_state (decision state machine)
    → (loop back to pick_chapter or assemble)
    → assemble (final proposal)
"""
from . import (  # noqa: F401
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
