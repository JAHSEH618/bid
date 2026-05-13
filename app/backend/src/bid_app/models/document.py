"""Document 表(§8 + PR-M7-1 v2 schema bump)。

``kind`` ∈ {tech_spec, scoring, template} 或 NULL(v2 后不再三选一,
保留字段做向后兼容)。
``markdown_path`` 是 markitdown 抽取后的 .md 落盘路径(便于排查)。
``structured_html`` 是 PR-M7-3 黑板的源文件(清洗后的 HTML)。
``tags`` 让用户自定义文档分类(替代 kind 主导)。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    # ⭐ PR-M7-1:nullable;v2 后无强约束,保留字段做向后兼容
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    markdown_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int] = mapped_column()
    extract_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # ⭐ PR-M7-1:用户自定义标签(列表),搜索 / 过滤
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # ⭐ PR-M7-3:抽取后的清洗 HTML;extract 节点写,blackboard 节点聚合
    structured_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ⭐ PR-M7-2 / D5:精确字节数,项目级总和限额用
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
