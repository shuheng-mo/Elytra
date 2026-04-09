"""Pydantic response models for the public API (PRD §5.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /api/query
# ---------------------------------------------------------------------------


class QueryResponse(BaseModel):
    success: bool
    query: str
    source: Optional[str] = None  # which datasource was queried
    dialect: Optional[str] = None  # which SQL dialect was generated
    intent: Optional[str] = None
    generated_sql: Optional[str] = None
    result: Optional[list[dict[str, Any]]] = None
    visualization_hint: Optional[str] = None
    final_answer: str = ""
    model_used: Optional[str] = None
    retry_count: int = 0
    latency_ms: int = 0
    token_count: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# /api/datasources
# ---------------------------------------------------------------------------


class DataSourceDescriptor(BaseModel):
    name: str
    dialect: str
    description: str = ""
    connected: bool = False
    table_count: Optional[int] = None
    is_default: bool = False


class DataSourcesResponse(BaseModel):
    datasources: list[DataSourceDescriptor]
    default: Optional[str] = None


# ---------------------------------------------------------------------------
# /api/schema
# ---------------------------------------------------------------------------


class ColumnDescriptor(BaseModel):
    name: str
    type: str = ""
    chinese_name: str = ""
    description: str = ""
    is_primary_key: bool = False
    enum_values: list[str] = Field(default_factory=list)


class TableDescriptor(BaseModel):
    table: str
    chinese_name: str = ""
    description: str = ""
    layer: str = ""
    columns: list[ColumnDescriptor] = Field(default_factory=list)
    common_queries: list[str] = Field(default_factory=list)


class SchemaResponse(BaseModel):
    layers: dict[str, list[TableDescriptor]]


# ---------------------------------------------------------------------------
# /api/history
# ---------------------------------------------------------------------------


class HistoryItem(BaseModel):
    id: int
    session_id: Optional[str] = None
    user_query: str
    intent: Optional[str] = None
    generated_sql: Optional[str] = None
    execution_success: Optional[bool] = None
    retry_count: int = 0
    model_used: Optional[str] = None
    latency_ms: Optional[int] = None
    token_count: Optional[int] = None
    estimated_cost: Optional[float] = None
    created_at: Optional[datetime] = None


class HistoryResponse(BaseModel):
    history: list[HistoryItem]
