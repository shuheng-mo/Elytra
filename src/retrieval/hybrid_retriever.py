"""Hybrid retrieval: BM25 + dense vector with min-max normalization."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.config import settings
from src.retrieval.bm25_index import BM25Index
from src.retrieval.embedder import Embedder
from src.retrieval.schema_loader import SchemaLoader, TableInfo

logger = logging.getLogger(__name__)

# System tables (query_history, schema_embeddings) should never be returned to
# the SQL generator as analytical targets.
EXCLUDED_LAYERS = {"SYSTEM"}


@dataclass
class RetrievalResult:
    table: TableInfo
    score: float          # combined, post-normalization
    bm25_score: float     # raw BM25 score
    vector_score: float   # raw cosine similarity (1 - distance)

    def to_dict(self) -> dict[str, Any]:
        d = self.table.to_dict()
        d["relevance_score"] = self.score
        d["bm25_score"] = self.bm25_score
        d["vector_score"] = self.vector_score
        return d


def _min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Map scores to [0, 1]. Returns empty dict on empty input."""
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        # All equal: collapse to 1.0 if positive, else 0.0
        return {k: (1.0 if hi > 0 else 0.0) for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridRetriever:
    """Combine BM25 and pgvector retrievers with weighted score fusion.

    The vector retriever is optional at runtime — if it raises (e.g. no DB,
    no embeddings indexed, no API key), we degrade gracefully to BM25-only.
    This makes local dev and unit tests work without standing up Postgres.

    Phase 2: each retriever instance is bound to a single ``source_name``.
    The schema retrieval node maintains one retriever per source via
    ``functools.lru_cache``. BM25 indexes the per-source ``TableInfo`` list
    that came out of ``SchemaLoader.load_from_connector``; vector search
    filters ``schema_embeddings.source_name`` to the same source.
    """

    def __init__(
        self,
        loader: SchemaLoader | None = None,
        embedder: Embedder | None = None,
        bm25_weight: float | None = None,
        vector_weight: float | None = None,
        *,
        source_name: str | None = None,
        tables: list[TableInfo] | None = None,
    ):
        self.source_name = source_name
        self.loader = loader or SchemaLoader()

        if tables is not None:
            # Caller already loaded tables (e.g. from a connector). Use as-is.
            source_tables = tables
        elif source_name is not None:
            cached = SchemaLoader.get_cached(source_name)
            if cached is None:
                raise RuntimeError(
                    f"no cached schema for source {source_name!r}; "
                    f"call SchemaLoader.load_from_connector() first"
                )
            source_tables = cached
        else:
            # Legacy YAML path
            source_tables = self.loader.load()

        self.tables = [t for t in source_tables if t.layer not in EXCLUDED_LAYERS]
        self.table_lookup: dict[str, TableInfo] = {t.name: t for t in self.tables}
        self.bm25 = BM25Index(self.tables)
        self.embedder = embedder or Embedder()
        self.bm25_weight = (
            bm25_weight if bm25_weight is not None else settings.bm25_weight
        )
        self.vector_weight = (
            vector_weight if vector_weight is not None else settings.vector_weight
        )

    def retrieve(self, query: str, top_n: int = 10) -> list[RetrievalResult]:
        # 1. BM25 retrieval over the in-memory index
        bm25_scores: dict[str, float] = dict(
            self.bm25.search_by_name(query, top_n=20)
        )

        # 2. Vector retrieval — degrade gracefully if unavailable
        vector_scores: dict[str, float] = {}
        try:
            for name, score in self.embedder.search(
                query, top_n=20, source_name=self.source_name
            ):
                if name in self.table_lookup:
                    vector_scores[name] = score
        except Exception as exc:  # noqa: BLE001 — intentional broad fallback
            logger.warning("Vector retrieval failed (%s); falling back to BM25 only.", exc)

        # 3. Min-max normalize each retriever independently
        bm25_norm = _min_max_normalize(bm25_scores)
        vector_norm = _min_max_normalize(vector_scores)

        # 4. Weighted merge
        candidate_names = set(bm25_norm) | set(vector_norm)
        merged: list[RetrievalResult] = []
        for name in candidate_names:
            table = self.table_lookup.get(name)
            if table is None:
                continue
            b = bm25_norm.get(name, 0.0)
            v = vector_norm.get(name, 0.0)
            combined = self.bm25_weight * b + self.vector_weight * v
            merged.append(
                RetrievalResult(
                    table=table,
                    score=combined,
                    bm25_score=bm25_scores.get(name, 0.0),
                    vector_score=vector_scores.get(name, 0.0),
                )
            )

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:top_n]
