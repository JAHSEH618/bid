"""arq WorkerSettings(§17.2 / D-AJ / D-AY / D-Z)。

⚠️ D-Z + D-AY:**所有任务 max_tries=1**(workflow 三类 + DOCX)。
arq 0.26.x 的 ``arq.worker.func`` 不是装饰器工厂——``coroutine`` 是必填位置
参数。spec §17.2 写法 ``@func(max_tries=1)`` 在该版本会抛
``TypeError: func() missing 1 required positional argument: 'coroutine'``。
正确做法:tasks.py 里保持 plain async function,本文件 ``functions=`` 列表
用 ``func(coroutine, max_tries=1)`` 逐个包装(返 ``Function`` 对象)。

⭐ D-AJ:``functions`` 与 ``cron_jobs`` 都直接放函数对象(不是字符串路径),
避免依赖 arq 字符串导入路径下 wrapped attribute 是否被发现的隐式行为。

⭐ D-AA:``max_jobs = max_concurrent_projects + 2``,给 DOCX task 留并发余量;
workflow 业务限流仍由 ``ACTIVE_SET`` 主导,两层独立。
"""
from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from ..config import settings
from ..services.concurrency import (
    cleanup_stale_chapters,
    cleanup_stale_docx_jobs,
    reconcile_periodic,
)
from .lifecycle import on_shutdown, on_startup
from .tasks import (
    generate_chapter_body_task,
    generate_docx_task,
    resume_review_task,
    retry_failed_chapter_task,
    start_workflow_task,
)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # ⭐ D-Z + D-AY:max_tries=1 在 wrapper 上指定;arq 0.26 API 要求位置参数
    # arq 的 ``func`` 返 untyped 对象(Function),用 list[Any] 收纳
    functions: ClassVar[list[Any]] = [
        func(start_workflow_task, max_tries=1),
        func(resume_review_task, max_tries=1),
        func(retry_failed_chapter_task, max_tries=1),
        func(generate_chapter_body_task, max_tries=1),
        func(generate_docx_task, max_tries=1),
    ]

    max_jobs = settings.max_concurrent_projects + 2
    job_timeout = 60 * 60 * 4  # 单 job 上限 4 小时(全章节累计)
    keep_result = 86400

    on_startup = on_startup
    on_shutdown = on_shutdown

    cron_jobs: ClassVar[list[Any]] = [
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
