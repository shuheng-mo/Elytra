"""Pydantic request models for the public API (PRD §5.3)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SqlDialect = Literal["postgresql", "hiveql", "sparksql"]


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="自然语言问题")
    session_id: Optional[str] = Field(None, max_length=64, description="会话ID，可选")
    dialect: SqlDialect = Field("postgresql", description="目标 SQL 方言")


class HistoryQueryParams(BaseModel):
    session_id: Optional[str] = None
    limit: int = Field(20, ge=1, le=200)
