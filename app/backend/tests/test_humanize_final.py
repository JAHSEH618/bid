from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("BID_APP_MASTER_KEY", "0" * 64)
os.environ.setdefault("JWT_SECRET", "1" * 64)

from bid_app.workflow.humanize import (
    _protect_markdown_blocks,
    _restore_markdown_blocks,
)


def test_protect_markdown_blocks_preserves_mermaid_and_tables() -> None:
    markdown = """# 技术方案

说明文字。

```mermaid
flowchart TD
A["质量经理"] --> B["质检专员"]
```

| 角色 | 职责 |
| --- | --- |
| 质量经理 | 统筹 |
"""

    protected, blocks = _protect_markdown_blocks(markdown)

    assert 'A["质量经理"]' not in protected
    assert "| 角色 | 职责 |" not in protected
    assert "@@PROTECTED_BLOCK_000@@" in protected
    assert "@@PROTECTED_BLOCK_001@@" in protected

    restored = _restore_markdown_blocks(protected, blocks)

    assert restored.strip() == markdown.strip()
