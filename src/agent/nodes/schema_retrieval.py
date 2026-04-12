"""Schema retrieval node — multi-source aware.

Wraps the retrieval stack: ``HybridRetriever`` (BM25 + pgvector) →
optional reranker → intent-aware top-K schemas, then writes them into
``AgentState`` as a list of dicts ready for the SQL generation prompt.

The reranker is disabled by default (``RERANKER_PROVIDER=none``) for
latency — BM25+vector score fusion is used directly. Set
``RERANKER_PROVIDER=llm`` or ``=local`` to re-enable.

There is one retriever instance per data source, lazily built from the
per-source schema cache populated during FastAPI startup. Vector search
is filtered to the active source via ``schema_embeddings.source_name``;
BM25 indexes only the tables from that source's ``connector.get_tables()``.

This node also pre-computes and caches the query embedding in state so
downstream nodes (``retrieve_experience``, ``save_experience``) can reuse
it without re-embedding.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from src.config import settings
from src.connectors.registry import ConnectorRegistry
from src.models.state import AgentState
from src.retrieval.embedder import get_embedder
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import RerankerLike, make_reranker
from src.retrieval.schema_loader import SchemaLoader

logger = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def _retriever_for_source(source_name: str) -> HybridRetriever:
    """Build (and cache) a retriever bound to a single data source.

    Requires that the schema for ``source_name`` has already been loaded
    into ``SchemaLoader._source_cache`` (normally via the FastAPI startup
    event). Raises ``RuntimeError`` if the cache is empty for that source.
    """
    cached = SchemaLoader.get_cached(source_name)
    if cached is None:
        raise RuntimeError(
            f"schema cache empty for source {source_name!r}; "
            f"the FastAPI startup event should have called "
            f"SchemaLoader.load_from_connector() for every configured source"
        )
    return HybridRetriever(source_name=source_name, tables=cached)


@lru_cache(maxsize=1)
def _reranker() -> RerankerLike | None:
    return make_reranker()


_INTENT_TOP_K = {
    "simple_query": 3,
    "aggregation": 5,
    "multi_join": 8,
    "exploration": 8,
}


def retrieve_schema_node(state: AgentState) -> dict:
    """LangGraph node: writes ``retrieved_schemas`` and ``query_embedding``."""
    user_query = state["user_query"]
    intent = state.get("intent", "aggregation")
    source_name = state.get("active_source") or ""
    if not source_name:
        registry = ConnectorRegistry.get_instance()
        source_name = registry.default_name() or ""
    if not source_name:
        logger.warning("no active_source and no default; returning empty schema list")
        return {"retrieved_schemas": []}

    # Pre-compute query embedding and cache it in state so downstream nodes
    # (retrieve_experience, save_experience) can reuse it without re-embedding.
    query_embedding = state.get("query_embedding")
    if query_embedding is None:
        try:
            query_embedding = get_embedder().embed(user_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("query embedding failed (%s)", exc)
            query_embedding = None

    # Intent-aware top-K: simple queries need fewer tables → shorter prompt
    # → faster LLM response.
    top_k = _INTENT_TOP_K.get(intent, settings.rerank_top_k)

    try:
        retriever = _retriever_for_source(source_name)
        candidates = retriever.retrieve(
            user_query, top_n=10, query_embedding=query_embedding,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hybrid retrieval failed for %s (%s); returning empty schema list.",
            source_name,
            exc,
        )
        return {"retrieved_schemas": [], "query_embedding": query_embedding}

    reranker = _reranker()
    if reranker is not None:
        try:
            reranked = reranker.rerank(user_query, candidates, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank failed (%s); using upstream order.", exc)
            reranked = candidates[:top_k]
    else:
        reranked = candidates[:top_k]

    return {
        "retrieved_schemas": [r.to_dict() for r in reranked],
        "query_embedding": query_embedding,
    }


def render_schemas_for_prompt(retrieved_schemas: list[dict]) -> str:
    """Convert retrieved schemas into a compact text block for SQL prompts."""
    if not retrieved_schemas:
        return "(no relevant tables found)"

    blocks: list[str] = []
    for s in retrieved_schemas:
        head = f"### {s.get('table')}  [{s.get('layer', '')}]  ({s.get('chinese_name', '')})"
        desc = s.get("description", "")
        col_lines = []
        for col in s.get("columns", []):
            line = f"  - {col['name']} {col.get('type', '')}: {col.get('chinese_name', '')} {col.get('description', '')}".rstrip()
            if col.get("enum_values"):
                line += f" [取值: {', '.join(map(str, col['enum_values']))}]"
            col_lines.append(line)
        blocks.append(head + "\n" + desc + "\n" + "\n".join(col_lines))

    return "\n\n".join(blocks)
