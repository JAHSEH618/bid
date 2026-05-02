"""arq WorkerSettings(§17.2 / D-AJ / D-AY / D-Z)。

⚠️ D-Z + D-AY:**所有任务 max_tries=1**(workflow 三类 + DOCX),通过
``@func(max_tries=...)`` 装饰器在 ``tasks.py`` 配置。

⭐ D-AJ:``functions`` 与 ``cron_jobs`` 都直接放函数对象(不是字符串路径),
避免依赖 arq 字符串导入路径下 wrapped attribute 是否被发现的隐式行为。

⭐ D-AA:``max_jobs = max_concurrent_projects + 2``,给 DOCX task 留并发余量;
workflow 业务限流仍由 ``ACTIVE_SET`` 主导,两层独立。
"""
from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from ..config import settings
from ..services.concurrency import (
    cleanup_stale_chapters,
    cleanup_stale_docx_jobs,
    reconcile_periodic,
)
from .lifecycle import on_shutdown, on_startup
from .tasks import (
    generate_docx_task,
    resume_review_task,
    retry_failed_chapter_task,
    start_workflow_task,
)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [
        start_workflow_task,
        resume_review_task,
        retry_failed_chapter_task,
        generate_docx_task,
    ]

    max_jobs = settings.max_concurrent_projects + 2
    job_timeout = 60 * 60 * 4  # 单 job 上限 4 小时(全章节累计)
    keep_result = 86400

    on_startup = on_startup
    on_shutdown = on_shutdown

    cron_jobs = [
        # 每分钟:清理 ACTIVE_SET 僵尸 + 同步 DB
        cron(
            reconcile_periodic,
            minute=set(range(0, 60)),
            unique=True,
            keep_result=0,
        ),
        # 每分钟:回滚卡中间态超 60s 的章节
        cron(
            cleanup_stale_chapters,
            minute=set(range(0, 60)),
            unique=True,
            keep_result=0,
        ),
        # 每 5 分钟:DOCX in-flight 超 30 分钟标 failed
        cron(
            cleanup_stale_docx_jobs,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            unique=True,
            keep_result=0,
        ),
    ]
