"""Cost estimation for LLM calls (Phase 2+).

Elytra's ``query_history`` table has an ``estimated_cost`` column but it was
never populated. This module fills that gap with a blended per-token price
table keyed by OpenRouter model slug (or underlying vendor prefix).

Rates are approximate — LLM pricing is a moving target. Treat these numbers
as a cost indicator for internal dashboards, not billing ground truth. If a
specific model isn't in the table we fall back to ``_DEFAULT_BLENDED_RATE``.

Token accounting in ``AgentState`` is a single cumulative ``token_count``
field that doesn't split input vs output (see ``src/agent/llm.py``), so we
can only use a blended rate per 1M tokens rather than input/output rates.
The blended number assumes a rough 3:1 input-to-output ratio, which matches
Elytra's typical prompt shape (long schema context + short SQL output).
"""

from __future__ import annotations

# Blended USD per 1M tokens. input*0.75 + output*0.25, rounded.
# Sources: public vendor pricing pages as of April 2026.
MODEL_PRICING_PER_1M: dict[str, float] = {
    # DeepSeek — cheap cheap cheap
    "deepseek/deepseek-chat": 0.48,
    "deepseek/deepseek-chat-v3-0324": 0.48,
    "deepseek/deepseek-v3": 0.48,
    "deepseek/deepseek-r1": 0.90,

    # Anthropic Claude
    "anthropic/claude-sonnet-4": 6.00,
    "anthropic/claude-sonnet-4-5": 6.00,
    "anthropic/claude-opus-4": 22.50,
    "anthropic/claude-opus-4-5": 22.50,
    "anthropic/claude-opus-4-6": 22.50,
    "anthropic/claude-haiku-4-5": 2.00,
    "anthropic/claude-3-5-sonnet": 6.00,
    "anthropic/claude-3-5-haiku": 1.25,

    # OpenAI
    "openai/gpt-4o": 4.38,
    "openai/gpt-4o-mini": 0.26,
    "openai/gpt-4-turbo": 15.00,
    "openai/o1-mini": 3.80,

    # Google Gemini
    "google/gemini-2.5-pro": 2.19,
    "google/gemini-2.5-flash": 0.31,
    "google/gemini-1.5-pro": 2.19,
    "google/gemini-1.5-flash": 0.23,

    # Others
    "meta-llama/llama-3.3-70b-instruct": 0.60,
    "qwen/qwen-2.5-72b-instruct": 0.60,
}

# Fallback blended rate: $2 per 1M tokens (rough mid-tier model price).
_DEFAULT_BLENDED_RATE_PER_1M: float = 2.00


def _normalize_slug(model: str | None) -> str:
    """Strip openrouter prefixes / trailing version tags for lookup."""
    if not model:
        return ""
    slug = model.strip().lower()
    # OpenRouter sometimes returns ":beta" or version suffixes
    for suffix in (":beta", ":free", ":latest"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug


def estimate_cost_usd(model: str | None, token_count: int | None) -> float:
    """Return estimated USD cost for a single agent run.

    Uses a blended per-token rate, so a run with 4000 total tokens on
    claude-sonnet-4 (~$6/M) costs ~$0.024.
    """
    if not token_count or token_count <= 0:
        return 0.0
    slug = _normalize_slug(model)
    rate = MODEL_PRICING_PER_1M.get(slug, _DEFAULT_BLENDED_RATE_PER_1M)
    return (token_count / 1_000_000.0) * rate
