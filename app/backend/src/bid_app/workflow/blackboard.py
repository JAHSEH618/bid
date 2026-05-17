"""HTML 黑板的读写 + 删除 (PR-M7-3 / D2)。

「黑板」= 项目级 HTML 文件 (``projects/{id}/blackboard.html``) +
``Project.blackboard_path`` DB 字段,两者保持一致。

写入语义 (D2 atomic):
1. 把内容写到 ``blackboard.html.tmp`` (tmp file in same dir as final)
2. ``fsync(tmp)``,再 ``os.replace(tmp, final)`` (POSIX 原子 rename)
3. 同事务里 ``UPDATE projects SET blackboard_path=...``
4. 任一步失败:tmp 清理 + 抛 ``BlackboardWriteFailed``

读取:从磁盘读,失败抛 ``BlackboardMissing``;DB 路径只是索引,真值
来自盘。这样 ``restore-backup.sh --with-files`` 跑完同步覆盖
``/var/lib/bid-app/projects/`` 后,DB 与盘一致即可恢复。

删除:Project 删除时级联调,优先 DB 字段拿路径,fallback 拼 settings
默认路径 (兜底:event listener 漏调时)。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import sqlalchemy as sa
import structlog

log = structlog.get_logger()

_BLACKBOARD_FILENAME = "blackboard.html"
_DEFAULT_PROJECTS_DIR = "/var/lib/bid-app/projects"


class BlackboardMissing(Exception):
    """读黑板时盘上不存在 (D-AY 不重试,UI 提示用户重跑 extract)。"""


class BlackboardWriteFailed(Exception):
    """tmp → atomic rename → DB commit 任一步失败,tmp 已清理。"""


def _projects_root() -> Path:
    """优先 ``settings.projects_dir``,settings 未初始化时 (测试 / CLI 在没
    PG 环境的子进程) 退到环境变量 / 内置默认,避免 import-time SystemExit。

    仅作 fallback;正常路径走 ``_project_dir_for(project_id)`` 从
    ``Project.dir_path`` 读真值,避免 runtime 切换 ``projects_dir`` 后黑板
    与上传材料分别落在新旧根目录的孤儿态。
    """
    try:
        from ..config import settings

        return Path(settings.projects_dir)
    except Exception:
        return Path(os.environ.get("PROJECTS_DIR", _DEFAULT_PROJECTS_DIR))


async def _project_dir_for(project_id: int) -> Path:
    """从 DB ``Project.dir_path`` 读真值;缺失时回退到 ``settings.projects_dir``
    拼路径。``Project.dir_path`` 在 ``create_project`` 时写入,后续不再改。"""
    try:
        from ..db import session_factory

        async with session_factory() as s:
            row = await s.execute(
                sa.text("SELECT dir_path FROM projects WHERE id=:i"),
                {"i": project_id},
            )
            dp = row.scalar_one_or_none()
            if dp:
                return Path(dp)
    except Exception:
        log.exception("blackboard_load_project_dir_failed", project_id=project_id)
    return _projects_root() / str(project_id)


def _project_dir(project_id: int) -> Path:
    """同步 fallback,仅在没法走 async 路径时调(目前没有调用方)。
    保留导出供 settings-only fallback 单测引用。"""
    return _projects_root() / str(project_id)


async def write_blackboard(project_id: int, html: str) -> Path:
    """原子写入 + DB commit。返回 final path。

    异常路径会清理 tmp,DB 不会留半成品 blackboard_path。
    """
    project_dir = await _project_dir_for(project_id)
    final = project_dir / _BLACKBOARD_FILENAME
    tmp = project_dir / f"{_BLACKBOARD_FILENAME}.tmp"

    def _write_disk() -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)

    try:
        await asyncio.to_thread(_write_disk)
    except Exception as e:
        # 兜底清理 tmp,避免后续 atomic rename 看到陈旧 tmp
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            log.exception("blackboard_tmp_cleanup_failed", project_id=project_id)
        raise BlackboardWriteFailed(
            f"failed to write blackboard for project {project_id}: {e}"
        ) from e

    try:
        from ..db import session_factory

        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET blackboard_path=:p WHERE id=:i"),
                {"p": str(final), "i": project_id},
            )
            await s.commit()
    except Exception as e:
        # 盘上已写但 DB 未更新 —— 下次 extract 会重写盘 + 重 UPDATE
        # 这里不回滚磁盘 (重写代价低,删了反而丢数据)
        log.exception(
            "blackboard_db_update_failed",
            project_id=project_id,
            path=str(final),
        )
        raise BlackboardWriteFailed(
            f"disk written but DB commit failed for project {project_id}: {e}"
        ) from e

    return final


async def read_blackboard(project_id: int) -> str:
    """从盘读黑板;不存在抛 ``BlackboardMissing``。"""
    project_dir = await _project_dir_for(project_id)
    path = project_dir / _BLACKBOARD_FILENAME
    if not path.exists():
        raise BlackboardMissing(f"blackboard.html missing on disk for project {project_id}")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def delete_blackboard(project_id: int) -> None:
    """Project 删除路径调;盘 + DB 双清。任一步失败仅 log,不阻塞主删除。"""
    project_dir = await _project_dir_for(project_id)
    path = project_dir / _BLACKBOARD_FILENAME
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        log.exception(
            "blackboard_disk_delete_failed",
            project_id=project_id,
            path=str(path),
        )

    try:
        from ..db import session_factory

        async with session_factory() as s:
            await s.execute(
                sa.text("UPDATE projects SET blackboard_path=NULL WHERE id=:i"),
                {"i": project_id},
            )
            await s.commit()
    except Exception:
        log.exception("blackboard_db_clear_failed", project_id=project_id)
