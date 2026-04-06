"""Phase 1 reranker: ask the cheap LLM to score candidates and resort.

Phase 2 will replace this with a local cross-encoder (`bge-reranker-v2-m3`).
The interface (``rerank(query, candidates) -> list[RetrievalResult]``) is
designed so that swap can be a drop-in.

Behavior:
    - On any failure (no API key, network error, malformed JSON), we fall back
      to returning the original candidates trimmed to ``top_k``. This keeps the
      pipeline alive even when the LLM is down.
"""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from src.agent.prompts.reranking import RERANK_PROMPT
from src.config import settings
from src.retrieval.hybrid_retriever import RetrievalResult

logger = logging.getLogger(__name__)


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
    """LLM-as-Reranker using an OpenAI-compatible chat completion endpoint."""

    def __init__(self, model: str | None = None):
        self.model = model or settings.default_cheap_model
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if "deepseek" in self.model.lower():
                if not settings.deepseek_api_key:
                    raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
                self._client = OpenAI(
                    api_key=settings.deepseek_api_key,
                    base_url="https://api.deepseek.com",
                )
            else:
                if not settings.openai_api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured.")
                self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

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
            resp = self.client.chat.completions.create(
                model=self.model,
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
