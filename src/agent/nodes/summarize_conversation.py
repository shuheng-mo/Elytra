"""Summarize conversation node — compress multi-turn history.

Runs at the very end of the success path (after save_experience /
generate_chart) and only when the session has enough history that a
summary would actually help. Calls the cheap LLM once per threshold
crossing and writes the result to ``conversation_summary`` via UPSERT.

State contract:
    Reads:  ``session_id``, ``conversation_history``, ``execution_success``
    Writes: (no state change; side effect is a DB row)

Design:
    * Summarization is opt-in by length — running it on every turn wastes
      tokens. We only fire once the session has >= 3 successful turns,
      and we re-summarize each time after that (not every turn, only
      when the summary is stale).
    * Uses the cheap model via ``chat_complete``, so no extra client
      setup. On failure the node logs and continues; a missing summary
      never breaks the graph.
"""

from __future__ import annotations

import logging

from src.agent.llm import chat_complete
from src.agent.prompts.summarization import build_summary_prompt
from src.config import settings
from src.db.connection import get_cursor
from src.models.state import AgentState

logger = logging.getLogger(__name__)

MIN_TURNS_FOR_SUMMARY = 3


def _fetch_recent_turns(session_id: str, limit: int) -> list[dict]:
    """Pull the most recent successful turns for this session, oldest first."""
    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(
                """
                SELECT user_query, generated_sql, created_at
                FROM query_history
                WHERE session_id = %s
                  AND execution_success = TRUE
                  AND generated_sql IS NOT NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = list(cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize: history fetch failed (%s)", exc)
        return []

    rows.reverse()
    return [
        {
            "user_query": r.get("user_query") or "",
            "generated_sql": r.get("generated_sql") or "",
        }
        for r in rows
    ]


def _upsert_summary(session_id: str, summary: str, turn_count: int) -> None:
    try:
        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                """
                INSERT INTO conversation_summary (session_id, summary, turn_count, last_updated)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (session_id) DO UPDATE
                SET summary = EXCLUDED.summary,
                    turn_count = EXCLUDED.turn_count,
                    last_updated = NOW()
                """,
                (session_id, summary, turn_count),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize: upsert failed (%s)", exc)


def summarize_conversation_node(state: AgentState) -> dict:
    """LangGraph node: compress recent turns into a context summary."""
    if not state.get("execution_success"):
        return {}

    session_id = state.get("session_id") or ""
    if not session_id:
        return {}

    # Pull turns INCLUDING the current one (which was just persisted as
    # part of _persist_history right before this node runs... actually
    # no, _persist_history runs AFTER the graph returns, so the current
    # turn isn't in query_history yet. We need MIN_TURNS-1 prior turns.
    turns = _fetch_recent_turns(session_id, limit=5)
    if len(turns) + 1 < MIN_TURNS_FOR_SUMMARY:
        return {}

    # Include the current turn at the end so the summary reflects the
    # newest state
    turns.append(
        {
            "user_query": state.get("user_query", ""),
            "generated_sql": state.get("generated_sql", ""),
        }
    )

    try:
        result = chat_complete(
            settings.default_cheap_model,
            build_summary_prompt(turns),
            temperature=0.2,
        )
        summary = (result.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize: LLM call failed (%s)", exc)
        return {}

    if not summary:
        return {}

    _upsert_summary(session_id, summary, len(turns))
    logger.info(
        "summarize: wrote %d-char summary for session %s (turn_count=%d)",
        len(summary),
        session_id[:8],
        len(turns),
    )
    return {}
