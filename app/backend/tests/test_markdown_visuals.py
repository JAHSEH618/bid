from __future__ import annotations

import json
import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("BID_APP_MASTER_KEY", "0" * 64)
os.environ.setdefault("JWT_SECRET", "1" * 64)

from bid_app.workflow.nodes.merge_chapter import _render_full_chapter
from bid_app.workflow.postprocess import strip_mermaid_decorations


def test_merge_chapter_unwraps_fenced_mermaid_visual() -> None:
    visuals = {
        "items": [
            {
                "title": "处理流程",
                "type": "mermaid",
                "anchor": "业务处理",
                "position": "after",
                "content": '```mermaid\nflowchart TD\nA["输入"] --> B["输出"]\nstyle A fill:#fff\n```',
            }
        ]
    }

    markdown = _render_full_chapter(
        chapter_index=0,
        chapter_title="总体方案",
        chapter_text="本章说明业务处理流程。",
        visuals_json_str=json.dumps(visuals, ensure_ascii=False),
    )

    assert markdown.count("```mermaid") == 1
    assert "```mermaid\n```mermaid" not in markdown
    assert 'flowchart TD\nA["输入"] --> B["输出"]' in markdown
    assert "style A fill" not in markdown
    assert "本章图表补充" not in markdown
    assert "插入位置" not in markdown


def test_merge_chapter_inserts_visual_after_anchor() -> None:
    visuals = {
        "items": [
            {
                "title": "处理流程",
                "type": "mermaid",
                "anchor": "业务处理流程",
                "position": "after",
                "content": 'flowchart TD\nA["输入"] --> B["输出"]',
            }
        ]
    }

    markdown = _render_full_chapter(
        chapter_index=0,
        chapter_title="总体方案",
        chapter_text="本章说明业务处理流程。\n\n后续说明继续展开。",
        visuals_json_str=json.dumps(visuals, ensure_ascii=False),
    )

    anchor_pos = markdown.index("业务处理流程")
    visual_pos = markdown.index("#### 图 1: 处理流程")
    next_pos = markdown.index("后续说明继续展开")
    assert anchor_pos < visual_pos < next_pos
    assert "本章图表补充" not in markdown
    assert "插入位置" not in markdown


def test_strip_mermaid_decorations_handles_spaced_or_tilde_fences() -> None:
    markdown = """~~~ mermaid
flowchart TD
A --> B
classDef bad fill:#eee,stroke:#333
class A bad
style B stroke:#111
~~~
"""

    cleaned = strip_mermaid_decorations(markdown)

    assert "flowchart TD" in cleaned
    assert "A --> B" in cleaned
    assert "classDef bad" not in cleaned
    assert "class A bad" not in cleaned
    assert "style B" not in cleaned


def test_merge_chapter_ignores_ascii_visuals() -> None:
    visuals = {
        "items": [
            {
                "title": "框线图",
                "type": "ascii",
                "anchor": "组织结构",
                "position": "after",
                "content": "+------+\\n| 模块 |\\n+------+",
            }
        ]
    }

    markdown = _render_full_chapter(
        chapter_index=1,
        chapter_title="运营管理规范体系",
        chapter_text="本章说明组织结构。",
        visuals_json_str=json.dumps(visuals, ensure_ascii=False),
    )

    assert "本章图表补充" not in markdown
    assert "+------+" not in markdown
