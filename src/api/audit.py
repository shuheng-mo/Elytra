"""Audit endpoints: query replay and usage statistics (Phase 2+)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from src.agent.graph import run_agent_async
from src.api.query import _compute_result_hash
from src.connectors.registry import ConnectorRegistry
from src.db.connection import get_cursor
from src.models.response import AuditStatsResponse, HistoryItem, ReplayResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["audit"])


def _load_history_row(history_id: int) -> dict[str, Any] | None:
    """Load a single query_history row by id."""
    with get_cursor(dict_rows=True) as cur:
        cur.execute(
            """
            SELECT id, session_id, user_query, intent, generated_sql,
                   execution_success, COALESCE(retry_count, 0) AS retry_count,
                   model_used, latency_ms, token_count, estimated_cost, created_at,
                   user_id, user_role, source_name, result_row_count, result_hash
            FROM query_history WHERE id = %s
            """,
            (history_id,),
        )
        return cur.fetchone()


def _row_to_history_item(row: dict[str, Any]) -> HistoryItem:
    return HistoryItem(
        id=row["id"],
        session_id=row.get("session_id"),
        user_query=row.get("user_query") or "",
        intent=row.get("intent"),
        generated_sql=row.get("generated_sql"),
        execution_success=row.get("execution_success"),
        retry_count=int(row.get("retry_count") or 0),
        model_used=row.get("model_used"),
        latency_ms=row.get("latency_ms"),
        token_count=row.get("token_count"),
        estimated_cost=(
            float(row["estimated_cost"]) if row.get("estimated_cost") is not None else None
        ),
        created_at=row.get("created_at"),
        user_id=row.get("user_id"),
        user_role=row.get("user_role"),
        source_name=row.get("source_name"),
        result_row_count=row.get("result_row_count"),
        result_hash=row.get("result_hash"),
    )


@router.post("/replay/{history_id}", response_model=ReplayResponse)
async def replay_query(history_id: int) -> ReplayResponse:
    """Re-execute a historical query and compare results."""
    try:
        row = _load_history_row(history_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db read failed: {exc}") from exc

    if row is None:
        raise HTTPException(status_code=404, detail=f"history_id {history_id} not found")

    original = _row_to_history_item(row)

    # Resolve data source for replay
    source_name = row.get("source_name")
    registry = ConnectorRegistry.get_instance()

    if not source_name:
        source_name = registry.default_name()
    if not source_name:
        raise HTTPException(status_code=400, detail="cannot determine source for replay")

    try:
        connector = registry.get(source_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"source '{source_name}' no longer available",
        ) from exc

    dialect = connector.get_dialect()

    try:
        replay_state = await run_agent_async(
            user_query=row["user_query"],
            session_id=f"replay-{history_id}",
            sql_dialect=dialect,
            active_source=source_name,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"replay agent failed: {exc}") from exc

    replay_hash = _compute_result_hash(replay_state.get("execution_result"))
    result_match = (
        original.result_hash is not None
        and replay_hash is not None
        and original.result_hash == replay_hash
    )

    diff_summary: str | None = None
    if not result_match and original.result_hash and replay_hash:
        diff_summary = (
            f"Result hash mismatch: original={original.result_hash[:12]}... "
            f"replay={replay_hash[:12]}..."
        )

    replay_info = {
        "sql": replay_state.get("generated_sql"),
        "result_hash": replay_hash,
        "executed_at": datetime.now(tz=None).isoformat(),
        "success": replay_state.get("execution_success", False),
        "row_count": replay_state.get("row_count", 0),
        "latency_ms": replay_state.get("latency_ms", 0),
    }

    return ReplayResponse(
        original=original,
        replay=replay_info,
        result_match=result_match,
        diff_summary=diff_summary,
    )


@router.get("/audit/stats", response_model=AuditStatsResponse)
def get_audit_stats(
    days: int = Query(7, ge=1, le=365, description="Number of days to aggregate"),
) -> AuditStatsResponse:
    """Return aggregate audit statistics for the last N days."""
    cutoff = datetime.now(tz=None) - timedelta(days=days)
    period_start = cutoff.strftime("%Y-%m-%d")
    period_end = datetime.now(tz=None).strftime("%Y-%m-%d")

    try:
        with get_cursor(dict_rows=True) as cur:
            # Overall stats
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE execution_success = TRUE) AS successes,
                    AVG(latency_ms) AS avg_latency,
                    COALESCE(SUM(estimated_cost), 0) AS total_cost
                FROM query_history
                WHERE created_at >= %s
                """,
                (cutoff,),
            )
            summary = cur.fetchone() or {}

            total = int(summary.get("total") or 0)
            successes = int(summary.get("successes") or 0)

            # By model
            cur.execute(
                """
                SELECT model_used, COUNT(*) AS cnt,
                       COALESCE(SUM(estimated_cost), 0) AS cost
                FROM query_history
                WHERE created_at >= %s AND model_used IS NOT NULL
                GROUP BY model_used ORDER BY cnt DESC
                """,
                (cutoff,),
            )
            by_model = {
                r["model_used"]: {"count": r["cnt"], "cost": float(r["cost"])}
                for r in cur.fetchall()
            }

            # By intent
            cur.execute(
                """
                SELECT intent, COUNT(*) AS cnt
                FROM query_history
                WHERE created_at >= %s AND intent IS NOT NULL
                GROUP BY intent ORDER BY cnt DESC
                """,
                (cutoff,),
            )
            by_intent = {r["intent"]: r["cnt"] for r in cur.fetchall()}

            # By source
            cur.execute(
                """
                SELECT source_name, COUNT(*) AS cnt
                FROM query_history
                WHERE created_at >= %s AND source_name IS NOT NULL
                GROUP BY source_name ORDER BY cnt DESC
                """,
                (cutoff,),
            )
            by_source = {r["source_name"]: r["cnt"] for r in cur.fetchall()}

            # By user
            cur.execute(
                """
                SELECT user_id, COUNT(*) AS cnt
                FROM query_history
                WHERE created_at >= %s AND user_id IS NOT NULL
                GROUP BY user_id ORDER BY cnt DESC
                """,
                (cutoff,),
            )
            by_user = {r["user_id"]: r["cnt"] for r in cur.fetchall()}

    except Exception as exc:  # noqa: BLE001
        logger.exception("audit stats query failed")
        raise HTTPException(status_code=500, detail=f"stats query failed: {exc}") from exc

    return AuditStatsResponse(
        period=f"{period_start} ~ {period_end}",
        total_queries=total,
        success_rate=round(successes / total, 4) if total > 0 else 0.0,
        avg_latency_ms=round(float(summary.get("avg_latency") or 0), 1),
        total_cost_usd=round(float(summary.get("total_cost") or 0), 6),
        by_model=by_model,
        by_intent=by_intent,
        by_source=by_source,
        by_user=by_user,
    )
