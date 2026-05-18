"""D-EH 锚点驱动可视化测试。"""
from __future__ import annotations

import json

from bid_app.workflow.nodes.gen_visuals import _scan_anchors
from bid_app.workflow.nodes.merge_chapter import _render_full_chapter
from bid_app.workflow.prompts.review_chapter_prompt import (
    build_architecture_messages,
    build_sequence_messages,
)


def test_scan_anchors_extracts_sequence_and_architecture() -> None:
    text = """## 3.2.2.1 用户认证模块

### 技术实现
...

### 典型业务流程

1. 商户登录与权限装载流程。流程目标是...处理步骤为...关键控制点包括...

对应时序图:商户登录与权限装载流程

2. 商户操作员切换门店流程。流程目标是...处理步骤为...关键控制点包括...

对应时序图:商户操作员切换门店流程
"""
    seq, arch = _scan_anchors(text)
    assert seq == ["商户登录与权限装载流程", "商户操作员切换门店流程"]
    assert arch == []


def test_scan_anchors_extracts_architecture() -> None:
    text = """## 3.2.1.1 应用架构方案

系统整体采用"接入层、网关层、业务服务层"七层架构。

接入层承载多端接入。
...
基础设施层负责数据库、中间件支撑。

系统整体架构如下图。

对应架构图:总体架构
"""
    seq, arch = _scan_anchors(text)
    assert seq == []
    assert arch == ["总体架构"]


def test_scan_anchors_deduplicates_repeated_names() -> None:
    text = "对应时序图:登录\n\n对应时序图:登录\n\n对应时序图:登录"
    seq, _ = _scan_anchors(text)
    assert seq == ["登录"]


def test_scan_anchors_handles_half_width_colon() -> None:
    """LLM 偶尔会写半角冒号,正则应兼容。"""
    text = "对应时序图: 半角名\n\n对应架构图: 总体架构"
    seq, arch = _scan_anchors(text)
    assert seq == ["半角名"]
    assert arch == ["总体架构"]


def test_scan_anchors_returns_empty_on_no_anchors() -> None:
    text = "## 1 这是一个普通章节\n\n没有任何锚点。\n"
    seq, arch = _scan_anchors(text)
    assert seq == []
    assert arch == []


def test_build_sequence_messages_contains_anchor_instruction() -> None:
    messages = build_sequence_messages(
        flow_name="商户登录流程",
        chapter_title="用户认证",
        chapter_body_md="...",
    )
    assert messages[0]["role"] == "system"
    assert "sequenceDiagram" in messages[0]["content"]
    # 用户消息要求把 anchor 设成完整 `对应时序图:<flow>`
    assert "对应时序图:商户登录流程" in messages[1]["content"]


def test_build_architecture_messages_lists_layers_in_order() -> None:
    messages = build_architecture_messages(
        layers=["接入层", "网关层", "业务服务层"],
        chapter_title="架构方案",
        chapter_body_md="...",
    )
    assert "接入层、网关层、业务服务层" in messages[1]["content"]
    assert "flowchart TD" in messages[0]["content"]
    assert "对应架构图:总体架构" in messages[0]["content"]


def test_merge_chapter_inserts_visual_after_anchor_line() -> None:
    """merge_chapter 的 substring anchor 匹配应能把 mermaid 块插到时序图锚点后。"""
    chapter_text = (
        "### 典型业务流程\n\n"
        "1. 商户登录流程。流程目标是A。处理步骤为B。关键控制点包括C。\n\n"
        "对应时序图:商户登录流程\n\n"
        "2. 第二个流程。流程目标是D。处理步骤为E。关键控制点包括F。\n\n"
        "对应时序图:第二个流程\n"
    )
    visuals = {
        "items": [
            {
                "title": "商户登录流程",
                "type": "mermaid",
                "anchor": "对应时序图:商户登录流程",
                "position": "after",
                "content": "sequenceDiagram\n  A->>B: x",
            },
            {
                "title": "第二个流程",
                "type": "mermaid",
                "anchor": "对应时序图:第二个流程",
                "position": "after",
                "content": "sequenceDiagram\n  C->>D: y",
            },
        ]
    }
    full = _render_full_chapter(
        chapter_index=0,
        chapter_title="测试模块",
        chapter_text=chapter_text,
        visuals_json_str=json.dumps(visuals),
    )
    # 章主标题
    assert full.startswith("## 第 1 章 · 测试模块")
    # 两张图都被插入,每张图块紧跟其锚点
    idx_a = full.find("对应时序图:商户登录流程")
    idx_b = full.find("对应时序图:第二个流程")
    idx_seq_a = full.find("A->>B: x")
    idx_seq_b = full.find("C->>D: y")
    assert idx_a < idx_seq_a < idx_b < idx_seq_b
