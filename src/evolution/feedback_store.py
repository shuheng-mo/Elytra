"""Feedback pool — user thumbs-up / thumbs-down signals on past queries.

Writer: ``POST /api/feedback`` stores a row with the current user query
and the resulting SQL, plus a positive / negative label and optional
explanation text.

Reader: ``retrieve_experience_node`` pulls the two categories separately:

    * ``retrieve_golden_examples`` — positive labels used as "do this"
      inspiration for similar future queries.
    * ``retrieve_negative_cases`` — negative labels surfaced as "don't do
      this" warnings. The similarity threshold is stricter here (0.8 vs.
      0.7 for golden) so we don't pollute the prompt with loosely-related
      cautions.
"""

from __future__ import annotations

import logging
from typing import Any

from src.db.connection import get_cursor

logger = logging.getLogger(__name__)


def _to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


class FeedbackStore:
    """Writer + reader for the ``query_feedback`` table."""

    def save_feedback(
        self,
        *,
        history_id: int,
        feedback_type: str,
        feedback_detail: str | None,
        user_query: str,
        generated_sql: str,
        source_name: str,
        intent: str,
        embedding: list[float],
    ) -> int | None:
        if feedback_type not in ("positive", "negative"):
            raise ValueError(f"feedback_type must be 'positive' or 'negative', got {feedback_type!r}")

        try:
            with get_cursor(dict_rows=False) as cur:
                cur.execute(
                    """
                    INSERT INTO query_feedback (
                        history_id, feedback_type, feedback_detail,
                        user_query, generated_sql, source_name, intent, embedding
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s::vector
                    )
                    RETURNING id
                    """,
                    (
                        history_id,
                        feedback_type,
                        feedback_detail,
                        user_query,
                        generated_sql,
                        source_name,
                        intent,
                        _to_pgvector(embedding),
                    ),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("query_feedback save failed: %s", exc)
            return None

    def retrieve_golden_examples(
        self,
        query_embedding: list[float],
        source_name: str,
        *,
        top_k: int = 2,
        similarity_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        return self._retrieve_by_type(
            query_embedding,
            source_name,
            feedback_type="positive",
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )

    def retrieve_negative_cases(
        self,
        query_embedding: list[float],
        source_name: str,
        *,
        top_k: int = 1,
        similarity_threshold: float = 0.8,
    ) -> list[dict[str, Any]]:
        return self._retrieve_by_type(
            query_embedding,
            source_name,
            feedback_type="negative",
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )

    def _retrieve_by_type(
        self,
        query_embedding: list[float],
        source_name: str,
        *,
        feedback_type: str,
        top_k: int,
        similarity_threshold: float,
    ) -> list[dict[str, Any]]:
        vec_literal = _to_pgvector(query_embedding)
        try:
            with get_cursor(dict_rows=True) as cur:
                cur.execute(
                    """
                    SELECT user_query, generated_sql,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM query_feedback
                    WHERE feedback_type = %s
                      AND source_name = %s
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        vec_literal,
                        feedback_type,
                        source_name,
                        vec_literal,
                        similarity_threshold,
                        vec_literal,
                        top_k,
                    ),
                )
                return [
                    {
                        "user_query": r["user_query"],
                        "generated_sql": r["generated_sql"],
                        "similarity": float(r["similarity"]),
                    }
                    for r in cur.fetchall()
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "query_feedback %s lookup failed: %s", feedback_type, exc
            )
            return []

    def stats(self) -> dict[str, Any]:
        """Aggregate counts for the audit dashboard."""
        try:
            with get_cursor(dict_rows=True) as cur:
                cur.execute(
                    """
                    SELECT feedback_type, COUNT(*) AS cnt
                    FROM query_feedback
                    GROUP BY feedback_type
                    """
                )
                counts = {r["feedback_type"]: int(r["cnt"]) for r in cur.fetchall()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("query_feedback stats failed: %s", exc)
            return {"total_positive": 0, "total_negative": 0, "approval_rate": 0.0}

        positive = counts.get("positive", 0)
        negative = counts.get("negative", 0)
        total = positive + negative
        return {
            "total_positive": positive,
            "total_negative": negative,
            "approval_rate": round(positive / total, 4) if total > 0 else 0.0,
        }
