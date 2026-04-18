"""Experience pool storage — self-correction pairs for few-shot recall.

The table ``experience_pool`` is created by migration 002 and gets its
``embedding`` column injected at runtime by
``Embedder.bootstrap_experience_tables()`` so the vector dim matches the
active ``EMBEDDING_MODEL``.

Storage path:
    save_experience node  →  ExperienceStore.save(record, embedding)

Retrieval path:
    retrieve_experience node  →  ExperienceStore.retrieve_similar(...)

This module uses sync psycopg2 via ``src.db.connection.get_cursor`` because
the rest of the persistence layer does — asyncpg would require a parallel
pool and a separate lifespan hook for no real throughput benefit (the
heavy stuff is LLM calls, not DB writes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.db.connection import get_cursor

logger = logging.getLogger(__name__)


@dataclass
class ExperienceRecord:
    user_query: str
    intent: str
    source_name: str
    failed_sql: str
    error_message: str
    error_type: str
    corrected_sql: str
    model_used: str = ""
    retry_count: int = 0


def _to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


class ExperienceStore:
    """Writer + reader for the ``experience_pool`` table."""

    def save(self, record: ExperienceRecord, embedding: list[float]) -> int | None:
        """Insert one correction pair and return its id (or None on failure)."""
        try:
            with get_cursor(dict_rows=False) as cur:
                cur.execute(
                    """
                    INSERT INTO experience_pool (
                        user_query, intent, source_name,
                        failed_sql, error_message, error_type,
                        corrected_sql, model_used, retry_count, embedding
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector
                    )
                    RETURNING id
                    """,
                    (
                        record.user_query,
                        record.intent,
                        record.source_name,
                        record.failed_sql,
                        record.error_message,
                        record.error_type,
                        record.corrected_sql,
                        record.model_used,
                        record.retry_count,
                        _to_pgvector(embedding),
                    ),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("experience_pool save failed: %s", exc)
            return None

    def retrieve_similar(
        self,
        query_embedding: list[float],
        source_name: str,
        *,
        top_k: int = 2,
        similarity_threshold: float = 0.75,
    ) -> list[dict[str, Any]]:
        """Return up to ``top_k`` past corrections matching the query.

        Filters by ``source_name`` — a PG correction pattern should never
        leak into a ClickHouse query. Similarity is cosine over the current
        embedder's space (not transferable across models; switching models
        requires re-embedding or the HNSW index will mix spaces).
        """
        vec_literal = _to_pgvector(query_embedding)
        try:
            with get_cursor(dict_rows=True) as cur:
                cur.execute(
                    """
                    SELECT id, user_query, failed_sql, error_message, error_type,
                           corrected_sql,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM experience_pool
                    WHERE source_name = %s
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, source_name, vec_literal, similarity_threshold, vec_literal, top_k),
                )
                rows = cur.fetchall()

            if not rows:
                return []

            # Bump times_retrieved for observability (best-effort, separate txn)
            try:
                ids = [r["id"] for r in rows]
                with get_cursor(dict_rows=False) as cur:
                    cur.execute(
                        "UPDATE experience_pool SET times_retrieved = times_retrieved + 1 WHERE id = ANY(%s)",
                        (ids,),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("times_retrieved bump failed: %s", exc)

            return [
                {
                    "id": r["id"],
                    "user_query": r["user_query"],
                    "failed_sql": r["failed_sql"],
                    "error_message": r["error_message"],
                    "error_type": r["error_type"],
                    "corrected_sql": r["corrected_sql"],
                    "similarity": float(r["similarity"]),
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("experience_pool lookup failed: %s", exc)
            return []

    def stats(self) -> dict[str, Any]:
        """Aggregate counts for the audit dashboard."""
        try:
            with get_cursor(dict_rows=True) as cur:
                cur.execute(
                    "SELECT COUNT(*) AS total, SUM(times_retrieved) AS retrievals FROM experience_pool"
                )
                total_row = cur.fetchone() or {}
                cur.execute(
                    """
                    SELECT error_type, COUNT(*) AS cnt
                    FROM experience_pool
                    WHERE error_type IS NOT NULL
                    GROUP BY error_type
                    ORDER BY cnt DESC
                    """
                )
                by_error = {r["error_type"]: int(r["cnt"]) for r in cur.fetchall()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("experience_pool stats failed: %s", exc)
            return {"total": 0, "retrievals": 0, "by_error_type": {}}

        return {
            "total": int(total_row.get("total") or 0),
            "retrievals": int(total_row.get("retrievals") or 0),
            "by_error_type": by_error,
        }
