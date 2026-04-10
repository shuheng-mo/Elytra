"""GET /api/history — read past query runs from query_history."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.db.connection import get_cursor
from src.models.response import HistoryItem, HistoryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history", response_model=HistoryResponse)
def get_history(
    session_id: Optional[str] = Query(None, max_length=64),
    limit: int = Query(20, ge=1, le=200),
) -> HistoryResponse:
    sql = """
        SELECT id, session_id, user_query, intent, generated_sql,
               execution_success, COALESCE(retry_count, 0) AS retry_count,
               model_used, latency_ms, token_count, estimated_cost, created_at,
               user_id, user_role, source_name, result_row_count, result_hash
        FROM query_history
        {where}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params: tuple
    if session_id:
        sql = sql.format(where="WHERE session_id = %s")
        params = (session_id, limit)
    else:
        sql = sql.format(where="")
        params = (limit,)

    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.exception("history read failed")
        raise HTTPException(status_code=500, detail=f"history read failed: {exc}") from exc

    items = [
        HistoryItem(
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
        for row in rows
    ]
    return HistoryResponse(history=items)
