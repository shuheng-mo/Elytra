"""Rule-based model router (PRD §5.5).

Implements a deterministic mapping from ``(intent, retrieved_schemas)`` →
model name. The graph calls :func:`route_model` once before SQL generation; on
self-correction retries it can be called again with ``retry_count`` to upgrade
to the strong model when the cheap one repeatedly fails.
"""

from __future__ import annotations

from typing import Any

from src.config import settings


def _distinct_tables(retrieved_schemas: list[dict[str, Any]]) -> int:
    return len({s.get("table") for s in retrieved_schemas if s.get("table")})


def estimate_complexity(intent: str, retrieved_schemas: list[dict[str, Any]]) -> int:
    """Heuristic 1-5 complexity score, used to populate AgentState.complexity_score."""
    n_tables = _distinct_tables(retrieved_schemas)
    if intent == "simple_query" and n_tables <= 1:
        return 1
    if intent == "aggregation" and n_tables <= 2:
        return 2
    if intent == "exploration":
        return 4
    if intent == "multi_join" or n_tables >= 3:
        return 5
    return 2


def route_model(
    intent: str,
    retrieved_schemas: list[dict[str, Any]],
    retry_count: int = 0,
) -> str:
    """Pick a model name based on the rules in PRD §5.5.

    Phase-2 fallback: when the cheap model has already failed twice, force an
    upgrade to the strong model regardless of intent.
    """
    if retry_count >= 2:
        return settings.default_strong_model

    n_tables = _distinct_tables(retrieved_schemas)

    if intent == "simple_query" and n_tables <= 1:
        return settings.default_cheap_model
    if intent == "aggregation" and n_tables <= 2:
        return settings.default_cheap_model
    if intent == "multi_join" or n_tables >= 3:
        return settings.default_strong_model
    if intent == "exploration":
        return settings.default_strong_model
    return settings.default_cheap_model
