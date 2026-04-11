"""Retrieve experience node — load dynamic few-shot examples for SQL gen.

Inserted between ``filter_by_permission`` and ``generate_sql`` so the
experience is scoped to the same tables the current user is allowed to
see. Failures are logged and swallowed — a retrieval hiccup should never
break the main agent flow.

State contract:
    Reads:  ``user_query``, ``active_source``, ``intent``
    Writes: ``dynamic_examples`` = {corrections, golden, negative}
"""

from __future__ import annotations

import logging
from functools import lru_cache

from src.evolution.experience_store import ExperienceStore
from src.evolution.feedback_store import FeedbackStore
from src.models.state import AgentState
from src.retrieval.embedder import Embedder

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def _experience_store() -> ExperienceStore:
    return ExperienceStore()


@lru_cache(maxsize=1)
def _feedback_store() -> FeedbackStore:
    return FeedbackStore()


def retrieve_experience_node(state: AgentState) -> dict:
    """LangGraph node: writes ``dynamic_examples``."""
    user_query = state.get("user_query", "")
    source_name = state.get("active_source") or ""
    if not user_query or not source_name:
        return {"dynamic_examples": {}}

    try:
        embedder = _embedder()
        query_embedding = embedder.embed(user_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieve_experience: embed failed (%s)", exc)
        return {"dynamic_examples": {}}

    corrections = _experience_store().retrieve_similar(
        query_embedding=query_embedding,
        source_name=source_name,
        top_k=2,
        similarity_threshold=0.75,
    )

    golden = _feedback_store().retrieve_golden_examples(
        query_embedding=query_embedding,
        source_name=source_name,
        top_k=2,
        similarity_threshold=0.7,
    )

    negative = _feedback_store().retrieve_negative_cases(
        query_embedding=query_embedding,
        source_name=source_name,
        top_k=1,
        similarity_threshold=0.8,
    )

    total = len(corrections) + len(golden) + len(negative)
    if total > 0:
        logger.info(
            "retrieve_experience: %d corrections, %d golden, %d negative",
            len(corrections),
            len(golden),
            len(negative),
        )

    return {
        "dynamic_examples": {
            "corrections": corrections,
            "golden": golden,
            "negative": negative,
        }
    }
