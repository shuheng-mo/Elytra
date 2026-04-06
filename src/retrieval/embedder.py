"""Embedding generation and pgvector-backed semantic search.

Three backends are supported, with a single ``Embedder`` facade selecting
between them at construction time:

* **openai**     — direct calls to ``api.openai.com`` (needs ``OPENAI_API_KEY``)
* **openrouter** — OpenAI-compatible calls to OpenRouter (needs
  ``OPENROUTER_API_KEY``); use ``vendor/model`` names like
  ``openai/text-embedding-3-large``
* **local**      — ``sentence-transformers`` running on CPU/GPU (no key);
  recommended models are ``BAAI/bge-small-zh-v1.5`` (512-dim, ~100MB) and
  ``BAAI/bge-m3`` (1024-dim, multilingual)

Provider auto-detection (when ``EMBEDDING_PROVIDER=auto``):
    1. Model name starts with ``BAAI/`` or contains ``bge``  → local
    2. Model name starts with ``openai/``                    → openrouter
       (or openai-direct if only ``OPENAI_API_KEY`` is set)
    3. Bare ``text-embedding-*``                             → openai-direct
       (falls back to openrouter if only OpenRouter is configured)

Phase 1 indexes one row per table (column-level rows are reserved for Phase 2).
Vectors are written as pgvector literals so we don't need an extra type
binding dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Protocol

from src.config import settings
from src.db.connection import get_cursor
from src.retrieval.schema_loader import TableInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known-model dim table — used when settings.embedding_dim is 0 (auto)
# ---------------------------------------------------------------------------

_KNOWN_MODEL_DIMS: dict[str, int] = {
    # OpenAI direct
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # OpenRouter (vendor-prefixed openai models)
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-ada-002": 1536,
    # Local — BGE family
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-large-zh-v1.5": 1024,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-m3": 1024,
}


def _resolve_dim(model: str, override: int = 0) -> int:
    if override > 0:
        return override
    if model in _KNOWN_MODEL_DIMS:
        return _KNOWN_MODEL_DIMS[model]
    # Strip vendor prefix and retry
    if "/" in model:
        bare = model.split("/", 1)[1]
        if bare in _KNOWN_MODEL_DIMS:
            return _KNOWN_MODEL_DIMS[bare]
    raise ValueError(
        f"Cannot auto-detect embedding dim for model {model!r}. "
        f"Set EMBEDDING_DIM in your environment."
    )


def _to_pgvector(vec: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


# ---------------------------------------------------------------------------
# Provider interface + 3 implementations
# ---------------------------------------------------------------------------


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class _OpenAIDirectProvider:
    """Calls api.openai.com directly. Used when an explicit OpenAI key is set."""

    name = "openai"

    def __init__(self, model: str, dim: int):
        from openai import OpenAI

        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured; cannot use the openai embedding backend."
            )
        self.model = model
        self.dim = dim
        self._client = OpenAI(api_key=settings.openai_api_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


class _OpenRouterProvider:
    """Calls OpenRouter's OpenAI-compatible /v1/embeddings endpoint.

    Models must be vendor-prefixed (e.g. ``openai/text-embedding-3-large``).
    Bare ``text-embedding-3-*`` names are auto-prefixed with ``openai/`` for
    convenience.
    """

    name = "openrouter"

    def __init__(self, model: str, dim: int):
        from openai import OpenAI

        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not configured; cannot use the openrouter embedding backend."
            )
        self.model = self._normalize(model)
        self.dim = dim
        self._client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )

    @staticmethod
    def _normalize(model: str) -> str:
        if "/" in model:
            return model
        if model.startswith("text-embedding-"):
            return f"openai/{model}"
        return model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


class _LocalProvider:
    """In-process sentence-transformers backend.

    Lazy-imports ``sentence_transformers`` so users who don't need local
    embedding don't pay the install cost. Install with::

        uv pip install sentence-transformers
    """

    name = "local"

    def __init__(self, model: str, dim: int):
        try:
            from sentence_transformers import SentenceTransformer  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install with: uv pip install sentence-transformers"
            ) from exc

        self.model = model
        self.dim = dim
        logger.info("Loading local embedding model %s …", model)
        self._encoder = SentenceTransformer(model)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._encoder.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _auto_select_provider(model: str) -> str:
    """Decide which backend to use based on the model name + available keys."""
    name = model.lower()

    # Local takes precedence whenever the model name screams "local"
    if model.startswith("BAAI/") or "bge" in name:
        return "local"

    # openai/* prefix is OpenRouter-style; pick OpenRouter if its key is set
    if model.startswith("openai/"):
        if settings.openrouter_api_key:
            return "openrouter"
        if settings.openai_api_key:
            return "openai"
        raise RuntimeError(
            f"Model {model!r} requires OPENROUTER_API_KEY or OPENAI_API_KEY."
        )

    # Bare openai-style name → prefer direct OpenAI, fall back to OpenRouter
    if name.startswith("text-embedding-"):
        if settings.openai_api_key:
            return "openai"
        if settings.openrouter_api_key:
            return "openrouter"
        raise RuntimeError(
            f"Model {model!r} requires OPENAI_API_KEY or OPENROUTER_API_KEY."
        )

    # Unknown model — pick the first available remote provider
    if settings.openai_api_key:
        return "openai"
    if settings.openrouter_api_key:
        return "openrouter"
    raise RuntimeError(
        f"No embedding provider could be auto-selected for model {model!r}."
    )


def make_embedding_provider(
    model: str | None = None,
    provider: str | None = None,
    dim: int | None = None,
) -> EmbeddingProvider:
    """Build an embedding provider for the given model.

    Args:
        model: model name; defaults to ``settings.embedding_model``.
        provider: ``auto`` / ``openai`` / ``openrouter`` / ``local``;
            defaults to ``settings.embedding_provider``.
        dim: override the auto-detected vector dim.
    """
    model = model or settings.embedding_model
    provider = (provider or settings.embedding_provider or "auto").lower()
    if provider == "auto":
        provider = _auto_select_provider(model)

    resolved_dim = _resolve_dim(model, override=dim or settings.embedding_dim)

    if provider == "openai":
        return _OpenAIDirectProvider(model, resolved_dim)
    if provider == "openrouter":
        return _OpenRouterProvider(model, resolved_dim)
    if provider == "local":
        return _LocalProvider(model, resolved_dim)
    raise ValueError(f"unknown embedding provider: {provider!r}")


# ---------------------------------------------------------------------------
# Embedder facade — what the rest of the app imports
# ---------------------------------------------------------------------------


class Embedder:
    """Provider-agnostic embedding + pgvector store."""

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        dim: int | None = None,
    ):
        self._provider: EmbeddingProvider | None = None
        self._model = model or settings.embedding_model
        self._provider_name = provider or settings.embedding_provider
        self._dim_override = dim

    @property
    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = make_embedding_provider(
                model=self._model,
                provider=self._provider_name,
                dim=self._dim_override,
            )
        return self._provider

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def dim(self) -> int:
        return self.provider.dim

    @property
    def backend(self) -> str:
        return self.provider.name

    # ----- embedding API -----------------------------------------------------

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self.provider.embed_batch(texts)

    # ----- pgvector index ----------------------------------------------------

    def bootstrap_table(self) -> None:
        """DROP and re-CREATE schema_embeddings with the current vector dim.

        Necessary when switching between providers/models with different dims
        (e.g. text-embedding-3-small=1536 → text-embedding-3-large=3072).
        Idempotent — safe to call on every startup.
        """
        dim = self.dim
        with get_cursor(dict_rows=False) as cur:
            cur.execute("DROP TABLE IF EXISTS schema_embeddings CASCADE")
            cur.execute(
                f"""
                CREATE TABLE schema_embeddings (
                    id              BIGSERIAL PRIMARY KEY,
                    table_name      VARCHAR(100) NOT NULL,
                    column_name     VARCHAR(100),
                    description     TEXT NOT NULL,
                    embedding       vector({dim}) NOT NULL,
                    metadata        JSONB
                )
                """
            )
            cur.execute(
                "CREATE INDEX schema_embeddings_hnsw "
                "ON schema_embeddings USING hnsw (embedding vector_cosine_ops)"
            )
        logger.info("schema_embeddings table bootstrapped (dim=%d)", dim)

    def index_tables(self, tables: list[TableInfo]) -> int:
        """Replace ``schema_embeddings`` rows with one per table."""
        if not tables:
            return 0

        texts = [t.to_text() for t in tables]
        vectors = self.embed_batch(texts)

        with get_cursor(dict_rows=False) as cur:
            cur.execute("TRUNCATE schema_embeddings RESTART IDENTITY")
            for tbl, text, vec in zip(tables, texts, vectors):
                metadata = {
                    "layer": tbl.layer,
                    "chinese_name": tbl.chinese_name,
                    "columns": [c.name for c in tbl.columns],
                    "row_count_approx": tbl.row_count_approx,
                    "update_frequency": tbl.update_frequency,
                    "embedding_provider": self.backend,
                    "embedding_model": self.model,
                }
                cur.execute(
                    """
                    INSERT INTO schema_embeddings
                        (table_name, column_name, description, embedding, metadata)
                    VALUES (%s, NULL, %s, %s::vector, %s::jsonb)
                    """,
                    (
                        tbl.name,
                        text,
                        _to_pgvector(vec),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
        return len(tables)

    def search(self, query: str, top_n: int = 20) -> list[tuple[str, float]]:
        """Cosine-similarity search over table-level rows."""
        vec_literal = _to_pgvector(self.embed(query))
        with get_cursor(dict_rows=True) as cur:
            cur.execute(
                """
                SELECT table_name,
                       1 - (embedding <=> %s::vector) AS score
                FROM schema_embeddings
                WHERE column_name IS NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_literal, vec_literal, top_n),
            )
            rows = cur.fetchall()
        return [(row["table_name"], float(row["score"])) for row in rows]
