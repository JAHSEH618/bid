"""项目级错误日志(JSONL,§19.2 / D-AE)。

为什么 JSONL 而不是 key=val 平文本(D-AE):
- traceback 是多行长字段,平文本格式打破"每行 < PIPE_BUF 原子"假设
- JSONL 把所有字段(含多行 traceback)序列化成单行 JSON,
  写入单次 syscall 内完成,跨进程并发 append 不交错
- 排查时 ``jq`` 直接处理,比正则切平文本简单

调用点(§19.2 表):services/llm.py 重试 / 终态、workflow/nodes/write_chapter.py
failed 分支、worker/tasks.py 顶层 except、services/docx_export.py。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from ..config import settings

log = structlog.get_logger()
_lock = asyncio.Lock()


async def append_error(project_dir: Path, message: str, **fields: Any) -> None:
    """JSONL 格式追加一行到 ``{project_dir}/errors.log``。

    永不抛异常(写日志失败不应影响业务流程),失败由 structlog 记录到 stdout。
    """
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        log_file = project_dir / "errors.log"
        record: dict[str, Any] = {
            "ts": datetime.now(ZoneInfo(settings.tz)).isoformat(timespec="seconds"),
            "msg": message,
            **{k: v for k, v in fields.items() if v is not None},
        }
        line = (
            json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))
            + "\n"
        )
        async with _lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        log.exception("append_error_failed", project_dir=str(project_dir))
