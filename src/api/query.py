"""POST /api/query — run a natural-language query through the agent.

Pipeline:
    1. Validate the request, resolve target data source via ConnectorRegistry.
    2. Invoke ``run_agent_async`` with the resolved source name + dialect.
    3. Persist the run to ``query_history`` (best effort — never blocks the response).
    4. Map ``AgentState`` → ``QueryResponse``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from src.agent.cost import estimate_cost_usd
from src.agent.graph import run_agent_async
from src.connectors.registry import ConnectorRegistry
from src.db.connection import get_cursor
from src.models.request import QueryRequest
from src.models.response import QueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


def _compute_result_hash(rows: list[dict[str, Any]] | None) -> str | None:
    """SHA-256 hash of the first 100 result rows for replay comparison."""
    if not rows:
        return None
    try:
        payload = json.dumps(rows[:100], sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()
    except (TypeError, ValueError):
        return None


def _persist_history(state: dict[str, Any]) -> None:
    """Best-effort write to ``query_history``. Swallows any error."""
    try:
        # Extract retrieved table names as JSON string
        schemas = state.get("retrieved_schemas") or []
        retrieved_tables = json.dumps(
            [s.get("table") or s.get("table_name", "") for s in schemas]
        ) if schemas else None

        # Serialize correction history
        correction_history = state.get("correction_history")
        correction_json = (
            json.dumps(correction_history, default=str)
            if correction_history else None
        )

        result_hash = _compute_result_hash(state.get("execution_result"))

        estimated_cost = estimate_cost_usd(
            state.get("model_used"),
            state.get("token_count") or 0,
        )

        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                """
                INSERT INTO query_history (
                    session_id, user_query, intent, generated_sql,
                    execution_success, retry_count, model_used,
                    latency_ms, token_count, estimated_cost,
                    user_id, user_role, source_name,
                    retrieved_tables, correction_history_json,
                    result_row_count, result_hash
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    state.get("session_id") or None,
                    state.get("user_query", ""),
                    state.get("intent"),
                    state.get("generated_sql"),
                    state.get("execution_success"),
                    state.get("retry_count", 0),
                    state.get("model_used"),
                    state.get("latency_ms", 0),
                    state.get("token_count", 0),
                    estimated_cost,
                    state.get("user_id") or None,
                    state.get("user_role") or None,
                    state.get("active_source") or None,
                    retrieved_tables,
                    correction_json,
                    state.get("row_count"),
                    result_hash,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_history persist failed: %s", exc)


@router.post("/query", response_model=QueryResponse)
async def post_query(req: QueryRequest) -> QueryResponse:
    registry = ConnectorRegistry.get_instance()
    if not registry.is_initialized:
        raise HTTPException(
            status_code=503,
            detail="connector registry not initialized",
        )

    # Resolve target source: explicit > default
    source_name = req.source or registry.default_name()
    if not source_name:
        raise HTTPException(
            status_code=400,
            detail="no `source` given and no default_source configured",
        )

    try:
        connector = registry.get(source_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    dialect = connector.get_dialect()

    try:
        final_state = await run_agent_async(
            user_query=req.query,
            session_id=req.session_id or "",
            sql_dialect=dialect,
            active_source=source_name,
            user_id=req.user_id or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent run failed")
        raise HTTPException(status_code=500, detail=f"agent failure: {exc}") from exc

    _persist_history(final_state)

    return QueryResponse(
        success=bool(final_state.get("execution_success", False)),
        query=req.query,
        source=source_name,
        dialect=dialect,
        intent=final_state.get("intent"),
        generated_sql=final_state.get("generated_sql"),
        result=final_state.get("execution_result"),
        visualization_hint=final_state.get("visualization_hint"),
        final_answer=final_state.get("final_answer", ""),
        model_used=final_state.get("model_used"),
        retry_count=int(final_state.get("retry_count", 0)),
        latency_ms=int(final_state.get("latency_ms", 0)),
        token_count=int(final_state.get("token_count", 0)),
        error=final_state.get("execution_error"),
        user_role=final_state.get("user_role") or None,
        tables_filtered=0,
        chart_spec=final_state.get("chart_spec"),
    )
