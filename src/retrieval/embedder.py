"""Embedding generation and pgvector-backed semantic search.

Five backends are supported, with a single ``Embedder`` facade selecting
between them at construction time:

* **openai**     — direct calls to ``api.openai.com`` (needs ``OPENAI_API_KEY``)
* **openrouter** — OpenAI-compatible calls to OpenRouter (needs
  ``OPENROUTER_API_KEY``); use ``vendor/model`` names like
  ``openai/text-embedding-3-large``
* **local**      — ``sentence-transformers`` running on CPU/GPU (no key);
  recommended models are ``BAAI/bge-small-zh-v1.5`` (512-dim, ~100MB) and
  ``BAAI/bge-m3`` (1024-dim, multilingual)
* **ollama**     — Ollama's OpenAI-compatible ``/v1/embeddings`` endpoint
  (needs ``OLLAMA_BASE_URL`` and Ollama ≥ 0.2); use ``ollama/<model>`` names
  like ``ollama/nomic-embed-text``
* **vllm**       — self-hosted vLLM OpenAI server (needs ``VLLM_BASE_URL``);
  use ``vllm/<model-id>`` names

Provider auto-detection (when ``EMBEDDING_PROVIDER=auto``):
    1. Model name starts with ``ollama/``                    → ollama
    2. Model name starts with ``vllm/``                      → vllm
    3. Model name starts with ``BAAI/`` or contains ``bge``  → local
    4. Model name starts with ``openai/``                    → openrouter
       (or openai-direct if only ``OPENAI_API_KEY`` is set)
    5. Bare ``text-embedding-*``                             → openai-direct
       (falls back to openrouter if only OpenRouter is configured)

Phase 1 indexes one row per table (column-level rows are reserved for Phase 2).
Vectors are written as pgvector literals so we don't need an extra type
binding dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Protocol

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
    # Ollama common embedding models (both bare and ollama/-prefixed forms)
    "nomic-embed-text": 768,
    "ollama/nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "ollama/mxbai-embed-large": 1024,
    "ollama/bge-m3": 1024,
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
        # local_files_only skips ~15 HuggingFace HEAD requests per load
        # (each 300-500ms) when the model is already cached on disk.
        try:
            self._encoder = SentenceTransformer(model, local_files_only=True)
        except OSError:
            # First-ever download: fall back to online mode
            logger.info("Model not cached locally, downloading from HuggingFace …")
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


class _OllamaProvider:
    """Embedding via Ollama's OpenAI-compatible ``/v1/embeddings`` endpoint.

    Requires Ollama ≥ 0.2 with a model pulled first, e.g.::

        ollama pull nomic-embed-text

    Model names may be passed as ``ollama/nomic-embed-text`` (the prefix is
    stripped before the API call) or as a bare name when the caller has
    already stripped the prefix.
    """

    name = "ollama"

    def __init__(self, model: str, dim: int):
        from openai import OpenAI

        if not settings.ollama_base_url:
            raise RuntimeError(
                "OLLAMA_BASE_URL is not configured; cannot use the ollama embedding backend."
            )
        # Strip ``ollama/`` prefix; Ollama doesn't expect vendor prefixes.
        effective = (
            model.split("/", 1)[1] if model.lower().startswith("ollama/") else model
        )
        self.model = effective
        self.dim = dim
        self._client = OpenAI(
            api_key="ollama",  # Ollama ignores it; SDK requires non-empty
            base_url=settings.ollama_base_url.rstrip("/") + "/v1",
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


class _VLLMProvider:
    """Embedding via a vLLM OpenAI-compatible server.

    Launch vLLM with an embedding-capable model::

        python -m vllm.entrypoints.openai.api_server --model <hf_model_id>

    Then set ``VLLM_BASE_URL`` (e.g. ``http://localhost:8000``) and use a
    model name of the form ``vllm/<whatever-model-id-vllm-was-started-with>``.
    """

    name = "vllm"

    def __init__(self, model: str, dim: int):
        from openai import OpenAI

        if not settings.vllm_base_url:
            raise RuntimeError(
                "VLLM_BASE_URL is not configured; cannot use the vllm embedding backend."
            )
        effective = (
            model.split("/", 1)[1] if model.lower().startswith("vllm/") else model
        )
        self.model = effective
        self.dim = dim
        self._client = OpenAI(
            api_key="vllm",
            base_url=settings.vllm_base_url.rstrip("/") + "/v1",
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _auto_select_provider(model: str) -> str:
    """Decide which backend to use based on the model name + available keys."""
    name = model.lower()

    # Explicit local-backend prefixes win immediately — user opted in by
    # writing ``ollama/foo`` or ``vllm/foo`` in their .env.
    if name.startswith("ollama/"):
        if settings.ollama_base_url:
            return "ollama"
        raise RuntimeError(
            f"Model {model!r} has ollama/ prefix but OLLAMA_BASE_URL is not set."
        )
    if name.startswith("vllm/"):
        if settings.vllm_base_url:
            return "vllm"
        raise RuntimeError(
            f"Model {model!r} has vllm/ prefix but VLLM_BASE_URL is not set."
        )

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
    if provider == "ollama":
        return _OllamaProvider(model, resolved_dim)
    if provider == "vllm":
        return _VLLMProvider(model, resolved_dim)
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

        Phase 2: the table now carries a ``source_name`` column so a single
        index can serve multiple data sources, with retrieval filtering by
        active source.
        """
        dim = self.dim
        with get_cursor(dict_rows=False) as cur:
            cur.execute("DROP TABLE IF EXISTS schema_embeddings CASCADE")
            cur.execute(
                f"""
                CREATE TABLE schema_embeddings (
                    id              BIGSERIAL PRIMARY KEY,
                    source_name     VARCHAR(100) NOT NULL,
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
            cur.execute(
                "CREATE INDEX schema_embeddings_source_idx "
                "ON schema_embeddings (source_name)"
            )
        logger.info("schema_embeddings table bootstrapped (dim=%d)", dim)

    def index_tables(
        self,
        tables: list[TableInfo],
        *,
        source_name: str,
    ) -> int:
        """Insert/replace ``schema_embeddings`` rows for a single source.

        Existing rows for the same source are deleted first; rows for other
        sources are left untouched. This makes ``--source <name>`` re-indexing
        cheap and safe.

        v0.5.0: we now index at BOTH table level (``column_name IS NULL``) and
        column level (one row per column). The column-level rows let the
        hybrid retriever boost parent tables that match the query via a
        specific field — e.g. "city 字段在哪些表里" hits the ``city`` column
        row directly, and the parent table gets a score boost through
        ``HybridRetriever._merge_column_hits``.
        """
        if not tables:
            return 0
        if not source_name:
            raise ValueError("source_name is required for index_tables()")

        # Build the full text list: one per table, then one per (table, column)
        table_texts = [t.to_text() for t in tables]
        column_rows: list[tuple[TableInfo, Any, str]] = []
        for tbl in tables:
            for col in tbl.columns:
                if not col.name:
                    continue
                desc_parts = [
                    f"{tbl.name}.{col.name}",
                    col.type or "",
                    col.chinese_name or "",
                    col.description or "",
                ]
                col_text = " ".join(p for p in desc_parts if p).strip()
                column_rows.append((tbl, col, col_text))

        all_texts = table_texts + [c[2] for c in column_rows]
        all_vectors = self.embed_batch(all_texts)
        table_vectors = all_vectors[: len(table_texts)]
        column_vectors = all_vectors[len(table_texts) :]

        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                "DELETE FROM schema_embeddings WHERE source_name = %s",
                (source_name,),
            )
            # Table-level rows
            for tbl, text, vec in zip(tables, table_texts, table_vectors):
                metadata = {
                    "layer": tbl.layer,
                    "chinese_name": tbl.chinese_name,
                    "columns": [c.name for c in tbl.columns],
                    "row_count_approx": tbl.row_count_approx,
                    "update_frequency": tbl.update_frequency,
                    "embedding_provider": self.backend,
                    "embedding_model": self.model,
                    "source_name": source_name,
                    "kind": "table",
                }
                cur.execute(
                    """
                    INSERT INTO schema_embeddings
                        (source_name, table_name, column_name, description, embedding, metadata)
                    VALUES (%s, %s, NULL, %s, %s::vector, %s::jsonb)
                    """,
                    (
                        source_name,
                        tbl.name,
                        text,
                        _to_pgvector(vec),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
            # Column-level rows
            for (tbl, col, text), vec in zip(column_rows, column_vectors):
                col_meta = {
                    "layer": tbl.layer,
                    "chinese_name": col.chinese_name,
                    "type": col.type,
                    "parent_table": tbl.name,
                    "embedding_provider": self.backend,
                    "embedding_model": self.model,
                    "source_name": source_name,
                    "kind": "column",
                }
                cur.execute(
                    """
                    INSERT INTO schema_embeddings
                        (source_name, table_name, column_name, description, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s::vector, %s::jsonb)
                    """,
                    (
                        source_name,
                        tbl.name,
                        col.name,
                        text,
                        _to_pgvector(vec),
                        json.dumps(col_meta, ensure_ascii=False),
                    ),
                )
        logger.info(
            "indexed %s: %d tables + %d columns",
            source_name,
            len(tables),
            len(column_rows),
        )
        return len(tables) + len(column_rows)

    def search(
        self,
        query: str,
        top_n: int = 20,
        *,
        source_name: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[str, float]]:
        """Cosine-similarity search over table-level rows.

        If ``source_name`` is given, results are restricted to that source.
        ``None`` searches across all sources (only useful for diagnostics).

        When ``query_embedding`` is provided, the embed step is skipped.
        """
        vec_literal = _to_pgvector(query_embedding or self.embed(query))
        with get_cursor(dict_rows=True) as cur:
            if source_name:
                cur.execute(
                    """
                    SELECT table_name,
                           1 - (embedding <=> %s::vector) AS score
                    FROM schema_embeddings
                    WHERE column_name IS NULL
                      AND source_name = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, source_name, vec_literal, top_n),
                )
            else:
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

    def search_mixed(
        self,
        query: str,
        top_n: int = 20,
        *,
        source_name: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Search returning both table-level and column-level hits separately.

        Returns a dict with two keys::

            {
                "tables":  [{table_name, score}, ...],
                "columns": [{table_name, column_name, score}, ...],
            }

        When ``query_embedding`` is provided, the embed step is skipped.
        """
        vec_literal = _to_pgvector(query_embedding or self.embed(query))
        tables_out: list[dict[str, Any]] = []
        cols_out: list[dict[str, Any]] = []

        with get_cursor(dict_rows=True) as cur:
            if source_name:
                cur.execute(
                    """
                    SELECT table_name, column_name,
                           1 - (embedding <=> %s::vector) AS score
                    FROM schema_embeddings
                    WHERE source_name = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, source_name, vec_literal, top_n * 2),
                )
            else:
                cur.execute(
                    """
                    SELECT table_name, column_name,
                           1 - (embedding <=> %s::vector) AS score
                    FROM schema_embeddings
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, vec_literal, top_n * 2),
                )
            for row in cur.fetchall():
                entry = {
                    "table_name": row["table_name"],
                    "score": float(row["score"]),
                }
                if row["column_name"] is None:
                    tables_out.append(entry)
                else:
                    entry["column_name"] = row["column_name"]
                    cols_out.append(entry)

        return {"tables": tables_out[:top_n], "columns": cols_out[:top_n]}

    # ----- v0.5.0: experience pool + feedback + conversation summary --------

    def bootstrap_experience_tables(self) -> None:
        """Ensure embedding columns on v0.5.0 tables match the current dim.

        The base DDL (``db/migrations/002_observability_and_evolution.sql``)
        creates ``experience_pool`` / ``query_feedback`` / ``conversation_summary``
        without any ``embedding`` column, because pgvector columns are
        strongly typed on dim and the dim is only known at runtime (after the
        embedder provider has been resolved).

        This method:
            1. Detects the current dim of any existing ``embedding`` column.
            2. If the dim doesn't match ``self.dim``, drops the stale column.
            3. Adds ``embedding vector(self.dim)`` if missing.
            4. Creates HNSW indexes.

        Only ``experience_pool`` and ``query_feedback`` get embedding columns;
        ``conversation_summary`` stores plain text (cross-session similarity
        retrieval is reserved for a future extension).
        """
        target_dim = self.dim
        with get_cursor(dict_rows=True) as cur:
            for table in ("experience_pool", "query_feedback"):
                # Does the column already exist, and what's its declared dim?
                cur.execute(
                    """
                    SELECT udt_name, atttypmod
                    FROM information_schema.columns
                    JOIN pg_attribute
                        ON pg_attribute.attrelid = (%s::regclass)
                       AND pg_attribute.attname = columns.column_name
                    WHERE columns.table_name = %s AND columns.column_name = 'embedding'
                    """,
                    (table, table),
                )
                existing = cur.fetchone()
                if existing is not None:
                    # Parse the dim from the udt_name (e.g. "vector" doesn't
                    # include it; need to query pg_type/pg_attribute). The
                    # simpler path: try a cheap `SELECT` on one row with a
                    # zero vector of target_dim — if the dim is wrong, pgvector
                    # raises and we drop+re-add. We instead just compare via
                    # a second query that hits ``format_type``.
                    cur.execute(
                        """
                        SELECT format_type(a.atttypid, a.atttypmod) AS col_type
                        FROM pg_attribute a
                        JOIN pg_class c ON a.attrelid = c.oid
                        WHERE c.relname = %s AND a.attname = 'embedding'
                        """,
                        (table,),
                    )
                    type_row = cur.fetchone()
                    current_type = (type_row or {}).get("col_type", "")
                    # current_type looks like "vector(512)" — parse the dim
                    current_dim: int | None = None
                    if current_type and "(" in current_type and ")" in current_type:
                        try:
                            current_dim = int(
                                current_type[
                                    current_type.rindex("(") + 1 : current_type.rindex(")")
                                ]
                            )
                        except ValueError:
                            current_dim = None

                    if current_dim != target_dim:
                        logger.info(
                            "%s.embedding dim mismatch (%s vs %d); dropping and re-adding",
                            table,
                            current_dim,
                            target_dim,
                        )
                        cur.execute(f"ALTER TABLE {table} DROP COLUMN embedding")
                    else:
                        # Already correct — ensure index exists and move on
                        cur.execute(
                            f"""
                            CREATE INDEX IF NOT EXISTS {table}_embedding_hnsw
                            ON {table} USING hnsw (embedding vector_cosine_ops)
                            """
                        )
                        continue

                # Add the embedding column with the current dim
                cur.execute(
                    f"ALTER TABLE {table} ADD COLUMN embedding vector({target_dim})"
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {table}_embedding_hnsw
                    ON {table} USING hnsw (embedding vector_cosine_ops)
                    """
                )
                logger.info(
                    "bootstrapped %s.embedding as vector(%d) + HNSW index",
                    table,
                    target_dim,
                )


# ---------------------------------------------------------------------------
# Module-level singleton — all consumers should call get_embedder() instead
# of constructing Embedder() directly. This ensures the expensive
# SentenceTransformer / OpenAI client is created exactly once.
# ---------------------------------------------------------------------------

_global_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Return (and lazily create) the process-wide Embedder singleton."""
    global _global_embedder  # noqa: PLW0603
    if _global_embedder is None:
        _global_embedder = Embedder()
    return _global_embedder
