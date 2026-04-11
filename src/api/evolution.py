"""GET /api/evolution/stats — self-evolution telemetry for the audit UI.

Three blocks:

1. ``experience_pool``: row count, total retrievals, breakdown by error_type
2. ``user_feedback``: positive / negative counts and approval rate
3. ``evolution_impact``: first-attempt success rate before vs. after the
   first experience_pool row was written, giving a rough "learning
   curve" number that the audit dashboard surfaces as "+N%".

The impact number is a heuristic — it assumes the experience pool only
gets meaningful after its first row and that query volume is evenly
distributed. If there are fewer than 10 queries either side of the
boundary, we return ``None`` rather than a misleading percentage.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.db.connection import get_cursor
from src.evolution.experience_store import ExperienceStore
from src.evolution.feedback_store import FeedbackStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["evolution"])


class EvolutionStatsResponse(BaseModel):
    experience_pool: dict[str, Any] = Field(default_factory=dict)
    user_feedback: dict[str, Any] = Field(default_factory=dict)
    evolution_impact: dict[str, Any] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def _experience_store() -> ExperienceStore:
    return ExperienceStore()


@lru_cache(maxsize=1)
def _feedback_store() -> FeedbackStore:
    return FeedbackStore()


def _compute_impact() -> dict[str, Any]:
    """Heuristic: first-attempt success rate before vs. after experience_pool started."""
    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute("SELECT MIN(created_at) AS first FROM experience_pool")
            first_row = cur.fetchone() or {}
            boundary = first_row.get("first")
            if boundary is None:
                return {
                    "before": None,
                    "after": None,
                    "improvement": None,
                    "note": "no experience yet",
                }

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE execution_success = TRUE AND retry_count = 0
                    ) AS first_shot
                FROM query_history
                WHERE created_at < %s
                """,
                (boundary,),
            )
            before = cur.fetchone() or {}
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE execution_success = TRUE AND retry_count = 0
                    ) AS first_shot
                FROM query_history
                WHERE created_at >= %s
                """,
                (boundary,),
            )
            after = cur.fetchone() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("evolution impact query failed: %s", exc)
        return {
            "before": None,
            "after": None,
            "improvement": None,
            "note": str(exc),
        }

    before_total = int(before.get("total") or 0)
    after_total = int(after.get("total") or 0)

    # Too few samples → don't fabricate a trend
    if before_total < 10 or after_total < 10:
        return {
            "before": None,
            "after": None,
            "improvement": None,
            "note": "too few samples",
            "before_total": before_total,
            "after_total": after_total,
        }

    before_rate = round(int(before["first_shot"]) / before_total, 4)
    after_rate = round(int(after["first_shot"]) / after_total, 4)
    improvement_pct = round((after_rate - before_rate) * 100, 1)

    return {
        "before": before_rate,
        "after": after_rate,
        "improvement": improvement_pct,
        "before_total": before_total,
        "after_total": after_total,
    }


@router.get("/evolution/stats", response_model=EvolutionStatsResponse)
def get_evolution_stats() -> EvolutionStatsResponse:
    try:
        return EvolutionStatsResponse(
            experience_pool=_experience_store().stats(),
            user_feedback=_feedback_store().stats(),
            evolution_impact=_compute_impact(),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"evolution stats failed: {exc}"
        ) from exc
