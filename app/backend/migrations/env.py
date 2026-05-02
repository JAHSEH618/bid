"""Alembic 运行环境(§9)。

关键:DSN 用同步 ``psycopg`` 驱动(``postgresql+psycopg://``);**不**走
asyncpg,因为 alembic 内部用同步连接执行 DDL。从 ``settings.alembic_sync_dsn``
取(由 config.py 用 POSTGRES_USER/PASSWORD/HOST/PORT/DB 组件字段拼装)。

⚠️ 不读 ``DATABASE_URL`` 环境变量 —— 项目不再把组装好的 DSN 写进 .env(D-W)。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 把 src/ 加进 sys.path,让 ``from bid_app.models import Base`` 能解析
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SRC_DIR = _BACKEND_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bid_app.config import settings  # noqa: E402
from bid_app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate / 各种 op 都从 Base.metadata 读
target_metadata = Base.metadata


def get_url() -> str:
    """优先用 settings.alembic_sync_dsn(组件字段拼装),允许通过环境变量
    ``ALEMBIC_DATABASE_URL`` 显式覆盖(只用于调试 / CI 重定向到测试库)。
    """
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        return override
    return settings.alembic_sync_dsn


config.set_main_option("sqlalchemy.url", get_url())


def run_migrations_offline() -> None:
    """生成 SQL 文本(--sql 模式)。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """正常 alembic upgrade head(连真 DB 执行 DDL)。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
