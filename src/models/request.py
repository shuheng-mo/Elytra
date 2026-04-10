"""Pydantic request models for the public API (PRD §5.3)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SqlDialect = Literal["postgresql", "duckdb", "starrocks", "hiveql", "sparksql"]


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="自然语言问题")
    session_id: Optional[str] = Field(None, max_length=64, description="会话ID，可选")
    source: Optional[str] = Field(
        None,
        max_length=64,
        description="数据源名称（对应 datasources.yaml 中的 name；省略则使用 default_source）",
    )
    user_id: Optional[str] = Field(
        None,
        max_length=64,
        description="用户标识，用于权限过滤（省略则使用 default_role）",
    )
    # `dialect` is retained for backwards compatibility with Phase 1 clients
    # but the dialect is now derived from the connector behind `source`.
    dialect: Optional[SqlDialect] = Field(
        None,
        description="（已弃用）SQL 方言，自动从数据源推断",
    )


class HistoryQueryParams(BaseModel):
    session_id: Optional[str] = None
    limit: int = Field(20, ge=1, le=200)
