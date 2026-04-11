"""Conversation API — list turns and clear a session.

GET  /api/conversation/{session_id} → { turns: [...], summary: str | null }
DELETE /api/conversation/{session_id} → clears summary + rewrites the
    session_id on query_history rows so they stay auditable but the agent
    no longer treats them as context (resolve_context filters by session_id
    equality, so a tombstoned id is sufficient — we deliberately don't
    delete the history rows themselves for audit integrity).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.db.connection import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["conversation"])


class ConversationTurn(BaseModel):
    history_id: int
    user_query: str
    generated_sql: Optional[str] = None
    execution_success: Optional[bool] = None
    created_at: Optional[datetime] = None


class ConversationResponse(BaseModel):
    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)
    summary: Optional[str] = None
    turn_count: int = 0


@router.get("/conversation/{session_id}", response_model=ConversationResponse)
def get_conversation(session_id: str) -> ConversationResponse:
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="invalid session_id")

    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(
                """
                SELECT id, user_query, generated_sql, execution_success, created_at
                FROM query_history
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT 50
                """,
                (session_id,),
            )
            turns = [
                ConversationTurn(
                    history_id=int(r["id"]),
                    user_query=r.get("user_query") or "",
                    generated_sql=r.get("generated_sql"),
                    execution_success=r.get("execution_success"),
                    created_at=r.get("created_at"),
                )
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT summary, turn_count FROM conversation_summary WHERE session_id = %s",
                (session_id,),
            )
            summary_row = cur.fetchone() or {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"conversation lookup failed: {exc}"
        ) from exc

    return ConversationResponse(
        session_id=session_id,
        turns=turns,
        summary=summary_row.get("summary"),
        turn_count=int(summary_row.get("turn_count") or len(turns)),
    )


@router.delete("/conversation/{session_id}")
def clear_conversation(session_id: str) -> dict[str, Any]:
    """Tombstone a session so the agent stops treating it as active context.

    We rename the session_id on existing query_history rows (append
    ``__cleared_<timestamp>``) rather than deleting them. The audit trail
    is preserved; only the agent's context-awareness changes.
    """
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="invalid session_id")

    tombstone_suffix = f"__cleared_{int(datetime.now(tz=None).timestamp())}"
    new_session_id = (session_id + tombstone_suffix)[:50]  # column is VARCHAR(50)

    try:
        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                "UPDATE query_history SET session_id = %s WHERE session_id = %s",
                (new_session_id, session_id),
            )
            affected = cur.rowcount
            cur.execute(
                "DELETE FROM conversation_summary WHERE session_id = %s",
                (session_id,),
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"conversation clear failed: {exc}"
        ) from exc

    return {"success": True, "cleared_turns": affected, "tombstone": new_session_id}
