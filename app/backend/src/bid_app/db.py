"""异步 SQLAlchemy engine + session_factory(§5 / §14.5)。

M0 只暴露 engine + session_factory(供 services/llm.py 等模块 lazily 调用)。
M1 (#6) 增 ``get_db`` FastAPI dependency。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
)

session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
