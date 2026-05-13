"""PR-M8-1 tests:material_understanding node + prompt build。

LLM 调用本身依赖 LiteLLM,这里只做轻量 happy path:验证 prompt 模板
能构造,且节点的 graph routing 逻辑 (_route_after_material_review)
按 decision 分支正确。
"""

from __future__ import annotations

from bid_app.workflow.graph import _route_after_material_review
from bid_app.workflow.prompts import material_understanding as prompt


def test_prompt_build_includes_blackboard() -> None:
    msgs = prompt.build_messages(blackboard_excerpt="<h2>测试材料</h2>")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "材料理解" in msgs[0]["content"]
    assert "测试材料" in msgs[1]["content"]


def test_prompt_build_with_revision_feedback() -> None:
    msgs = prompt.build_messages(
        blackboard_excerpt="<p>x</p>",
        revision_feedback="评分要点漏读了合规项",
    )
    user = msgs[1]["content"]
    assert "评分要点漏读了合规项" in user
    assert "完整重写后的 JSON" in user


def test_prompt_empty_blackboard_fallback() -> None:
    msgs = prompt.build_messages(blackboard_excerpt="")
    user = msgs[1]["content"]
    assert "(空)" in user


def test_route_pass_goes_to_outline() -> None:
    state = {"_material_review_decision": "pass"}
    assert _route_after_material_review(state) == "generate_outline"


def test_route_skip_goes_to_outline() -> None:
    state = {"_material_review_decision": "skip"}
    assert _route_after_material_review(state) == "generate_outline"


def test_route_revise_loops_back() -> None:
    state = {"_material_review_decision": "revise"}
    assert (
        _route_after_material_review(state) == "material_understanding"
    )


def test_route_default_is_outline() -> None:
    """无 _material_review_decision (老 checkpoint) 默认走 outline。"""
    state: dict[str, object] = {}
    assert _route_after_material_review(state) == "generate_outline"
