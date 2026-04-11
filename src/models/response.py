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
    # Phase 2+ permission fields
    user_role: Optional[str] = None
    tables_filtered: int = 0
    # Phase 2+ chart spec
    chart_spec: Optional[dict[str, Any]] = None


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
    user_managed: bool = False  # True if added at runtime via API (deletable)


class DataSourcesResponse(BaseModel):
    datasources: list[DataSourceDescriptor]
    default: Optional[str] = None


class DialectFieldDescriptor(BaseModel):
    key: str
    label: str
    type: str  # string | password | int | path | select
    required: bool = False
    default: Optional[object] = None
    placeholder: Optional[str] = None
    help: Optional[str] = None


class DialectSchema(BaseModel):
    dialect: str
    label: str
    description: str = ""
    fields: list[DialectFieldDescriptor]


class DialectSchemasResponse(BaseModel):
    dialects: list[DialectSchema]


class CreateDataSourceResponse(BaseModel):
    success: bool
    datasource: DataSourceDescriptor
    indexing_status: str = "pending"  # pending | running | success | skipped | failed
    indexing_error: Optional[str] = None


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
    # Phase 2+ audit fields
    user_id: Optional[str] = None
    user_role: Optional[str] = None
    source_name: Optional[str] = None
    result_row_count: Optional[int] = None
    result_hash: Optional[str] = None


class HistoryResponse(BaseModel):
    history: list[HistoryItem]


# ---------------------------------------------------------------------------
# /api/replay
# ---------------------------------------------------------------------------


class ReplayResponse(BaseModel):
    original: HistoryItem
    replay: dict[str, Any] = Field(default_factory=dict)
    result_match: bool = False
    diff_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# /api/audit/stats
# ---------------------------------------------------------------------------


class AuditStatsResponse(BaseModel):
    period: str = ""
    total_queries: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    by_model: dict[str, Any] = Field(default_factory=dict)
    by_intent: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_user: dict[str, int] = Field(default_factory=dict)
    top_errors: list[dict[str, Any]] = Field(default_factory=list)
