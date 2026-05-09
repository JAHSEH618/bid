"""LangGraph 节点(§10.2 11 个节点 + 1 个 graph)。

extract_documents → generate_outline → parse_outline → outline_review (interrupt)
  → pick_chapter → chapter_generate_gate → write_chapter → gen_visuals → merge_chapter
  → human_review (interrupt) → update_state
  → (loop back to pick_chapter or assemble)
  → assemble (final proposal)
"""
from . import (  # noqa: F401
    assemble,
    chapter_generate_gate,
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
