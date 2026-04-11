"""LangGraph AgentState definition (PRD §5.1).

Phase 1 fields are required; Phase 2 fields default to empty / None and are
populated by Day-8+ features (multi-turn dialogue, conversation summarization).
"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict

Intent = Literal[
    "simple_query",
    "aggregation",
    "multi_join",
    "exploration",
    "clarification",
]

SqlDialect = Literal["postgresql", "duckdb", "starrocks", "hiveql", "sparksql"]


class CorrectionAttempt(TypedDict, total=False):
    sql: str
    error: str
    feedback: str
    error_type: str  # ErrorType enum value; populated by self_correction node


class AgentState(TypedDict, total=False):
    # ----- Inputs -----
    user_query: str
    session_id: str
    sanitized_query: str  # output of sanitize_user_query, used by the agent
    sanitizer_violations: list[str]  # rule names triggered during sanitization

    # ----- Intent classification -----
    intent: Intent
    clarification_question: Optional[str]

    # ----- Schema retrieval -----
    retrieved_schemas: list[dict[str, Any]]  # [{table, columns, relevance_score, ...}]

    # ----- Model routing -----
    model_used: str
    complexity_score: int  # 1-5

    # ----- Data source routing -----
    active_source: str  # name of the connector to run against (matches datasources.yaml)

    # ----- SQL generation -----
    generated_sql: str
    sql_dialect: SqlDialect

    # ----- Execution -----
    execution_success: bool
    execution_result: Optional[list[dict[str, Any]]]
    execution_error: Optional[str]
    row_count: int

    # ----- Self-correction -----
    retry_count: int
    correction_history: list[CorrectionAttempt]

    # ----- Output -----
    final_answer: str
    visualization_hint: Optional[str]  # bar_chart / line_chart / table / number
    latency_ms: int
    token_count: int

    # ----- Permission (Phase 2+) -----
    user_id: str
    user_role: str

    # ----- Chart (Phase 2+) -----
    chart_spec: Optional[dict[str, Any]]

    # ----- Phase 2 extensions (placeholders) -----
    conversation_history: list[dict[str, Any]]
    context_summary: Optional[str]

    # ----- v0.5.0 additions -----
    dynamic_examples: dict[str, Any]  # {corrections, golden, negative} from experience pool
    experience_saved: bool  # True if this run contributed a row to experience_pool
    history_id: Optional[int]  # id returned by query_history INSERT ... RETURNING id


def make_initial_state(
    user_query: str,
    session_id: str = "",
    sql_dialect: SqlDialect = "postgresql",
    active_source: str = "",
    user_id: str = "",
) -> AgentState:
    """Build a fresh AgentState with sensible defaults for every field."""
    return AgentState(
        user_query=user_query,
        session_id=session_id,
        sanitized_query=user_query,
        sanitizer_violations=[],
        intent="simple_query",
        clarification_question=None,
        retrieved_schemas=[],
        model_used="",
        complexity_score=1,
        active_source=active_source,
        generated_sql="",
        sql_dialect=sql_dialect,
        execution_success=False,
        execution_result=None,
        execution_error=None,
        row_count=0,
        retry_count=0,
        correction_history=[],
        final_answer="",
        visualization_hint=None,
        latency_ms=0,
        token_count=0,
        user_id=user_id,
        user_role="",
        chart_spec=None,
        conversation_history=[],
        context_summary=None,
        dynamic_examples={},
        experience_saved=False,
        history_id=None,
    )
