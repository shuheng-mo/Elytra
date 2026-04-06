"""Schema retrieval node.

Wraps the Step-3 retrieval stack: ``HybridRetriever`` (BM25 + pgvector) →
``LLMReranker`` → top-k schemas, then writes them into ``AgentState`` as a
list of dicts ready for the SQL generation prompt.

The retriever is constructed once at module load (data dictionary is small)
and reused across requests.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from src.config import settings
from src.models.state import AgentState
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import LLMReranker

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _retriever() -> HybridRetriever:
    return HybridRetriever()


@lru_cache(maxsize=1)
def _reranker() -> LLMReranker:
    return LLMReranker()


def retrieve_schema_node(state: AgentState) -> dict:
    """LangGraph node: writes ``retrieved_schemas``."""
    user_query = state["user_query"]
    try:
        candidates = _retriever().retrieve(user_query, top_n=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hybrid retrieval failed (%s); returning empty schema list.", exc)
        return {"retrieved_schemas": []}

    try:
        reranked = _reranker().rerank(user_query, candidates, top_k=settings.rerank_top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank failed (%s); using upstream order.", exc)
        reranked = candidates[: settings.rerank_top_k]

    return {"retrieved_schemas": [r.to_dict() for r in reranked]}


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
