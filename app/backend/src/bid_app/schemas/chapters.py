"""Pydantic v2 IO schemas — chapters 相关。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    decision: Literal["approve", "revise", "skip"]
    feedback: str | None = Field(None, max_length=4000)
