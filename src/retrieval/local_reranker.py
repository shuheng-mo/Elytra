"""Local cross-encoder reranker backed by ``sentence_transformers``.

Drop-in replacement for ``LLMReranker`` — exposes the same
``rerank(query, candidates, top_k)`` signature and can be loaded via
``make_reranker()`` in :mod:`src.retrieval.reranker`.

The model (default ``BAAI/bge-reranker-v2-m3``) is lazy-loaded on first use
so tests and import-time code paths don't pay the ~560MB download cost.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from src.config import settings
from src.retrieval.hybrid_retriever import RetrievalResult

logger = logging.getLogger(__name__)


def _build_doc(c: RetrievalResult) -> str:
    """Stringify a candidate for cross-encoder scoring.

    We include only the fields that the LLM reranker also used — table
    name, layer, Chinese name, description, and a handful of common
    queries — because the cross-encoder works best on short, semantically
    dense texts.
    """
    tbl = c.table
    parts: list[str] = [
        f"{tbl.name} ({tbl.layer} / {tbl.chinese_name})",
        tbl.description or "",
    ]
    if tbl.common_queries:
        parts.append("常用查询: " + "; ".join(tbl.common_queries[:3]))
    # Include a few column hints so column-level relevance bleeds into
    # the table-level score. Keep it short to stay inside the 512-token
    # context of bge-reranker-v2-m3.
    col_hints = [
        f"{c.name}({c.chinese_name})".strip()
        for c in tbl.columns[:8]
        if c.name
    ]
    if col_hints:
        parts.append("列: " + ", ".join(col_hints))
    return " | ".join(p for p in parts if p)


class LocalReranker:
    """Cross-encoder reranker (``BAAI/bge-reranker-v2-m3`` by default).

    The model is loaded on first call and kept in-process for the life of
    the agent. Failing to load (no ``sentence_transformers`` installed, no
    network for the initial download) is surfaced as a ``RuntimeError`` so
    :func:`make_reranker` can fall back to the LLM reranker.
    """

    def __init__(self, model: str | None = None):
        self.model_name = model or getattr(
            settings, "reranker_model", "BAAI/bge-reranker-v2-m3"
        )
        self._encoder: Any = None
        # Guards lazy load against concurrent callers. Without this the Rust
        # tokenizer inside sentence_transformers raises "Already borrowed"
        # when two threads init the same CrossEncoder at once.
        self._load_lock = threading.Lock()
        # Serializes prediction calls. CrossEncoder.predict is not thread-safe
        # on the Rust tokenizer side — sharing one instance across LangGraph
        # async edges means we need an explicit lock.
        self._predict_lock = threading.Lock()

    @property
    def encoder(self) -> Any:
        if self._encoder is None:
            with self._load_lock:
                if self._encoder is None:
                    try:
                        from sentence_transformers import CrossEncoder  # noqa: WPS433
                    except ImportError as exc:
                        raise RuntimeError(
                            "sentence-transformers is not installed — cannot use LocalReranker. "
                            "Install with: uv sync --extra local-embed"
                        ) from exc

                    logger.info("Loading local reranker model %s …", self.model_name)
                    self._encoder = CrossEncoder(self.model_name)
                    logger.info("CrossEncoder model loaded (%s)", self.model_name)
        return self._encoder

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        if not candidates:
            return []
        top_k = top_k or settings.rerank_top_k

        pairs = [(query, _build_doc(c)) for c in candidates]
        try:
            encoder = self.encoder  # force lazy load before taking predict lock
            with self._predict_lock:
                scores = encoder.predict(pairs, show_progress_bar=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "local rerank failed (%s); using upstream order.", exc
            )
            return candidates[:top_k]

        # Zip scores back to candidates, sort by cross-encoder score, tiebreak on
        # the original hybrid retrieval score.
        scored = sorted(
            zip(candidates, (float(s) for s in scores)),
            key=lambda pair: (pair[1], pair[0].score),
            reverse=True,
        )
        return [c for c, _ in scored[:top_k]]
