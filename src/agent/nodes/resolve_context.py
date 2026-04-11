"""Resolve context node — load recent turns for multi-turn dialog.

Inserted between ``classify_intent`` and ``retrieve_schema`` so the
downstream schema retrieval can see what the user was just asking about
(helps when the current turn uses pronouns like "他们" / "这些" /
"上一条").

State contract:
    Reads:  ``session_id``
    Writes: ``conversation_history`` (most recent successful turns, oldest
            first) and ``context_summary`` (if a ``conversation_summary``
            row exists for this session).

Design choices (pragmatic, not aesthetic):
    * Only SUCCESSFUL past turns are loaded — failed attempts don't
      provide useful context, and the SQL from a failed turn is wrong
      by definition.
    * Limit is 3 turns. More than that bloats the prompt without
      measurable accuracy gains on NL2SQL queries (which rarely need
      deep history).
    * No LLM call — pure DB read. Summarization happens in a
      separate node on the way out of the graph.
"""

from __future__ import annotations

import logging
from typing import Any

from src.db.connection import get_cursor
from src.models.state import AgentState

logger = logging.getLogger(__name__)

MAX_RECENT_TURNS = 3
MAX_QUERY_CHARS = 400
MAX_SQL_CHARS = 800


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def resolve_context_node(state: AgentState) -> dict:
    """LangGraph node: populates ``conversation_history`` and ``context_summary``."""
    session_id = state.get("session_id") or ""
    if not session_id:
        return {"conversation_history": [], "context_summary": None}

    history: list[dict[str, Any]] = []
    summary: str | None = None

    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(
                """
                SELECT user_query, generated_sql, final_answer_excerpt, created_at
                FROM (
                    SELECT user_query, generated_sql,
                           LEFT(COALESCE(generated_sql, ''), %s) AS final_answer_excerpt,
                           created_at
                    FROM query_history
                    WHERE session_id = %s
                      AND execution_success = TRUE
                      AND generated_sql IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT %s
                ) recent
                ORDER BY created_at ASC
                """,
                (MAX_SQL_CHARS, session_id, MAX_RECENT_TURNS),
            )
            for row in cur.fetchall():
                history.append(
                    {
                        "user_query": _truncate(row.get("user_query") or "", MAX_QUERY_CHARS),
                        "generated_sql": _truncate(row.get("generated_sql") or "", MAX_SQL_CHARS),
                    }
                )

            cur.execute(
                "SELECT summary FROM conversation_summary WHERE session_id = %s",
                (session_id,),
            )
            summary_row = cur.fetchone()
            if summary_row:
                summary = summary_row.get("summary")
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve_context: DB read failed (%s)", exc)
        return {"conversation_history": [], "context_summary": None}

    if history or summary:
        logger.info(
            "resolve_context: %d recent turns, summary=%s",
            len(history),
            "yes" if summary else "no",
        )

    return {
        "conversation_history": history,
        "context_summary": summary,
    }
