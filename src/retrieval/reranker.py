"""Reranker interface + two implementations.

Historical note:
    v0.1–0.4 shipped only the LLM reranker (``LLMReranker``). v0.5.0 adds a
    local cross-encoder (``LocalReranker``) and a factory (``make_reranker``)
    so deployments can pick auto / local / llm via ``settings.reranker_provider``.
    The ``rerank(query, candidates, top_k) -> list[RetrievalResult]`` signature
    is stable across both implementations so the schema retrieval node
    doesn't care which backend it got.

Behavior:
    - On any failure (no API key, network error, malformed JSON), we fall back
      to returning the original candidates trimmed to ``top_k``. This keeps the
      pipeline alive even when the LLM is down.

v0.5.0 bug fix: ``LLMReranker.client`` now delegates to
``src.agent.llm._resolve_client`` so OpenRouter-only deployments no longer
fall through to a hardcoded OpenAI/DeepSeek client that would fail.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from src.agent.prompts.reranking import RERANK_PROMPT
from src.config import settings
from src.retrieval.hybrid_retriever import RetrievalResult

logger = logging.getLogger(__name__)


class RerankerLike(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]: ...


def _build_candidate_block(candidates: list[RetrievalResult]) -> str:
    lines: list[str] = []
    for idx, c in enumerate(candidates, 1):
        lines.append(
            f"[{idx}] {c.table.name}  ({c.table.layer} / {c.table.chinese_name})"
        )
        lines.append(f"    描述: {c.table.description}")
        if c.table.common_queries:
            lines.append(
                "    常用查询: " + "; ".join(c.table.common_queries[:3])
            )
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Best-effort JSON parser tolerant of code fences and stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise


class LLMReranker:
    """LLM-as-Reranker using an OpenAI-compatible chat completion endpoint.

    Client resolution delegates to ``src.agent.llm._resolve_client`` so the
    OpenRouter-first routing rules apply. This fixes a v0.4 bug where the
    reranker bypassed OpenRouter and only worked when OPENAI_API_KEY or
    DEEPSEEK_API_KEY was set directly.
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.default_cheap_model
        self._client: Any = None
        self._effective_model: str | None = None

    def _ensure_client(self) -> tuple[Any, str]:
        if self._client is None:
            # Local import avoids an import cycle: src.agent.llm imports
            # retrieval utilities that live one level up.
            from src.agent.llm import _resolve_client

            self._client, self._effective_model = _resolve_client(self.model)
        assert self._effective_model is not None
        return self._client, self._effective_model

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        if not candidates:
            return []
        top_k = top_k or settings.rerank_top_k

        prompt = RERANK_PROMPT.format(
            query=query,
            candidates=_build_candidate_block(candidates),
        )

        try:
            client, effective_model = self._ensure_client()
            # The ``chat.completions.create`` path works for both OpenAI-like
            # clients and the Anthropic adapter defined in src.agent.llm.
            resp = client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            content = resp.choices[0].message.content or ""
            data = _extract_json(content)
            score_map: dict[str, float] = {}
            for item in data.get("scores", []) or []:
                name = item.get("table")
                if not name:
                    continue
                try:
                    score_map[name] = float(item.get("score", 0))
                except (TypeError, ValueError):
                    continue
        except Exception as exc:  # noqa: BLE001 — fall back to upstream order
            logger.warning("LLM rerank failed (%s); using upstream order.", exc)
            return candidates[:top_k]

        # Sort by LLM score, breaking ties with the upstream hybrid score
        reranked = sorted(
            candidates,
            key=lambda c: (score_map.get(c.table.name, -1.0), c.score),
            reverse=True,
        )
        return reranked[:top_k]


# ---------------------------------------------------------------------------
# Factory — picks between LocalReranker (cross-encoder) and LLMReranker
# ---------------------------------------------------------------------------


def make_reranker(provider: str | None = None) -> RerankerLike | None:
    """Construct a reranker per ``settings.reranker_provider``.

    ``none`` (default) disables reranking entirely — the hybrid retriever's
    BM25+vector score fusion is used directly. This saves ~12s per query
    by avoiding an LLM round-trip.

    ``auto`` tries ``LocalReranker`` first and falls back to ``LLMReranker``.
    ``local`` and ``llm`` force one of the two.
    """
    choice = (provider or getattr(settings, "reranker_provider", "none") or "none").lower()

    if choice == "none":
        return None

    if choice == "llm":
        return LLMReranker()

    if choice in ("local", "auto"):
        try:
            from src.retrieval.local_reranker import LocalReranker

            reranker = LocalReranker()
            # Touch .encoder to force the lazy import+load; catch failures
            # here so "auto" can fall back to LLM instead of crashing at
            # first use.
            if choice == "local":
                return reranker
            # Auto mode: probe sentence_transformers existence cheaply without
            # downloading the full model. If the package isn't installed the
            # import error surfaces immediately; model load cost is paid on
            # first rerank call.
            try:
                import sentence_transformers  # noqa: F401
            except ImportError:
                logger.info(
                    "sentence-transformers not available; falling back to LLMReranker"
                )
                return LLMReranker()
            return reranker
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LocalReranker unavailable (%s); falling back to LLMReranker", exc
            )
            return LLMReranker()

    raise ValueError(
        f"unknown reranker provider: {choice!r} (expected none/auto/local/llm)"
    )
