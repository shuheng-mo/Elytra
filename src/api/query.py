"""POST /api/query — run a natural-language query through the agent.

Pipeline:
    1. Validate the request
    2. Invoke ``run_agent`` (which compiles & runs the LangGraph state machine)
    3. Persist the run to ``query_history`` (best effort — never blocks the response)
    4. Map ``AgentState`` → ``QueryResponse``
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from src.agent.graph import run_agent
from src.db.connection import get_cursor
from src.models.request import QueryRequest
from src.models.response import QueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


def _persist_history(state: dict[str, Any]) -> None:
    """Best-effort write to ``query_history``. Swallows any error."""
    try:
        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                """
                INSERT INTO query_history (
                    session_id, user_query, intent, generated_sql,
                    execution_success, retry_count, model_used,
                    latency_ms, token_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_history persist failed: %s", exc)


@router.post("/query", response_model=QueryResponse)
def post_query(req: QueryRequest) -> QueryResponse:
    if req.dialect != "postgresql":
        # Phase 1 only ships PostgreSQL; HiveQL/SparkSQL come in Phase 2 §7.3
        raise HTTPException(
            status_code=400,
            detail=f"dialect '{req.dialect}' is not supported in Phase 1",
        )

    try:
        final_state = run_agent(
            user_query=req.query,
            session_id=req.session_id or "",
            sql_dialect=req.dialect,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent run failed")
        raise HTTPException(status_code=500, detail=f"agent failure: {exc}") from exc

    _persist_history(final_state)

    return QueryResponse(
        success=bool(final_state.get("execution_success", False)),
        query=req.query,
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
    )
