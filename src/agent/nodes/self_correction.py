"""Self-correction node.

Records the failed attempt into ``correction_history`` and increments
``retry_count``. The graph routes back to ``generate_sql`` afterwards, where
the SQL generator detects the non-zero retry counter and switches to the
self-correction prompt.

Keeping this node tiny (no LLM call of its own) means the actual rewrite
prompt lives in exactly one place — ``sql_generator`` — which makes the
retry loop easier to reason about.
"""

from __future__ import annotations

import logging

from src.models.state import AgentState
from src.observability.errors import classify_error

logger = logging.getLogger(__name__)


def self_correction_node(state: AgentState) -> dict:
    """LangGraph node: bumps retry counter and appends to correction history.

    Also classifies the error into a canonical ``ErrorType`` and stashes it
    on the correction record so downstream consumers (experience pool,
    audit ``top_errors``) don't re-parse the message.
    """
    failed_sql = state.get("generated_sql", "")
    error = state.get("execution_error", "") or "(unknown error)"
    error_type = classify_error(error)
    history = list(state.get("correction_history", []))
    history.append(
        {
            "sql": failed_sql,
            "error": error,
            "feedback": "retrying with self-correction prompt",
            "error_type": error_type.value,
        }
    )
    new_retry = state.get("retry_count", 0) + 1
    logger.info(
        "self_correction: retry %d, error_type=%s, last error: %s",
        new_retry,
        error_type.value,
        error[:120],
    )
    return {
        "retry_count": new_retry,
        "correction_history": history,
    }
