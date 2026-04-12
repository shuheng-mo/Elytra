"""Save experience node — persist successful self-correction pairs.

Runs on the success path, after ``format_result``, only if this query
was actually self-corrected (``retry_count > 0``). The first entry in
``correction_history`` is the initial failure; the final
``generated_sql`` is the fix that worked. That pair goes into the
experience pool, keyed on the user's query embedding.

State contract:
    Reads:  ``retry_count``, ``execution_success``, ``correction_history``,
            ``user_query``, ``generated_sql``, ``active_source``, ``intent``,
            ``model_used``, ``query_embedding``
    Writes: ``experience_saved`` (bool)
"""

from __future__ import annotations

import logging
from functools import lru_cache

from src.evolution.experience_store import ExperienceRecord, ExperienceStore
from src.models.state import AgentState
from src.observability.errors import ErrorType, classify_error
from src.retrieval.embedder import get_embedder

logger = logging.getLogger(__name__)


def _embedder():
    return get_embedder()


@lru_cache(maxsize=1)
def _store() -> ExperienceStore:
    return ExperienceStore()


def save_experience_node(state: AgentState) -> dict:
    """LangGraph node: persists a correction pair if the run self-corrected."""
    if not state.get("execution_success"):
        return {"experience_saved": False}
    if state.get("retry_count", 0) <= 0:
        return {"experience_saved": False}

    history = state.get("correction_history") or []
    if not history:
        return {"experience_saved": False}

    first_attempt = history[0]
    failed_sql = first_attempt.get("sql") or ""
    error_message = first_attempt.get("error") or ""
    error_type_val = first_attempt.get("error_type")
    if not error_type_val:
        error_type_val = classify_error(error_message).value
    # Avoid storing meaningless rows
    if not failed_sql or not error_message:
        return {"experience_saved": False}

    record = ExperienceRecord(
        user_query=state.get("user_query", ""),
        intent=state.get("intent", ""),
        source_name=state.get("active_source", ""),
        failed_sql=failed_sql,
        error_message=error_message,
        error_type=str(error_type_val or ErrorType.UNKNOWN.value),
        corrected_sql=state.get("generated_sql", ""),
        model_used=state.get("model_used", ""),
        retry_count=int(state.get("retry_count", 0)),
    )

    # Reuse embedding cached by retrieve_schema_node if available
    embedding = state.get("query_embedding")
    if embedding is None:
        try:
            embedding = _embedder().embed(record.user_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_experience: embed failed (%s)", exc)
            return {"experience_saved": False}

    row_id = _store().save(record, embedding)
    if row_id is None:
        logger.warning("save_experience: persist returned None")
        return {"experience_saved": False}

    logger.info(
        "save_experience: stored correction pair id=%d (error_type=%s)",
        row_id,
        record.error_type,
    )
    return {"experience_saved": True}
