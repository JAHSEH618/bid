"""Application settings (D-W: DSN composed from components, not read literally)."""
from __future__ import annotations

import re
import sys
from urllib.parse import quote

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 端口与时区
    app_port: int = 12123
    tz: str = "Asia/Shanghai"

    # 数据库组件字段(D-W:DSN 由本类拼装)
    postgres_user: str
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # 加密密钥
    bid_app_master_key: str
    jwt_secret: str

    # 默认 admin
    admin_default_username: str = "admin"
    admin_default_password: str = "admin123"

    # LLM 模型(D1)
    llm1_outline_model: str = "dashscope/deepseek-v4-flash"
    llm2_chapter_model: str = "dashscope/qwen3.6-max-preview"
    llm3_visuals_model: str = "dashscope/qwen3.6-flash"

    # 业务参数
    max_concurrent_projects: int = 10
    max_file_size_mb: int = 50
    daily_upload_quota_mb: int = 500
    single_chapter_timeout_seconds: int = 600
    llm_retry_max: int = 2
    llm_retry_backoff_s: str = "2,5"
    global_rate_limit: str = "100/minute"
    login_fail_max_per_minute: int = 5
    login_lock_seconds: int = 300

    # 路径
    projects_dir: str = "/var/lib/bid-app/projects"
    backups_dir: str = "/var/lib/bid-app/backups"
    templates_dir: str = "/app/backend/templates"

    # 日志
    log_level: str = "INFO"

    # M1 dev/test stub(D-EC):指向 seed admin user.id;M2 起完整 deps.py 上线后忽略
    bid_app_dev_user_id: str | None = None

    # M1 测试用 fake LLM 开关(§22)
    bid_app_fake_llm: bool = False

    @field_validator("bid_app_master_key", "jwt_secret")
    @classmethod
    def _hex64(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", v):
            raise ValueError("must be 64 hex chars (run scripts/gen-secrets.sh)")
        return v.lower()

    @field_validator("postgres_password")
    @classmethod
    def _not_placeholder(cls, v: str) -> str:
        if not v or v == "__GENERATE_ME__":
            raise ValueError(
                "POSTGRES_PASSWORD must be set (run scripts/gen-secrets.sh)"
            )
        return v

    @property
    def database_url(self) -> str:
        """SQLAlchemy 异步引擎用(asyncpg)。密码 URL-quote 防 @/&/# 等特殊字符。"""
        pwd = quote(self.postgres_password, safe="")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def test_database_url(self) -> str:
        """D-DM:独立测试库 URL,数据库名固定加 `_test` 后缀。

        本 property 不读环境变量,所以 conftest 直接 ``settings.test_database_url``
        就能拿到一致的派生 URL。
        """
        pwd = quote(self.postgres_password, safe="")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}_test"
        )

    @property
    def langgraph_dsn(self) -> str:
        """langgraph-checkpoint-postgres 用(psycopg3,纯 DSN 不带 SQLAlchemy 前缀)。"""
        pwd = quote(self.postgres_password, safe="")
        return (
            f"postgresql://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def alembic_sync_dsn(self) -> str:
        """Alembic 用同步驱动 psycopg3。"""
        pwd = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


try:
    settings = Settings()
except Exception as e:
    print(f"Config validation failed: {e}", file=sys.stderr)
    sys.exit(1)
