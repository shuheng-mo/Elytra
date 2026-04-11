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


class CreateDataSourceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$",
                      description="连接器唯一名称（字母开头，仅含字母数字下划线）")
    dialect: str = Field(..., description="连接器方言，如 postgresql / duckdb / starrocks")
    description: str = Field("", max_length=200)
    connection: dict = Field(..., description="连接参数，字段取决于 dialect 的 schema")
    run_bootstrap: bool = Field(
        True,
        description="是否在连接成功后立即索引 schema embeddings（否则需要手动跑 bootstrap）",
    )
