"""Shared chat-completion helper for agent nodes.

The helper is OpenRouter-first: when an OpenRouter API key is configured,
**every** chat completion is routed through ``https://openrouter.ai/api/v1``
regardless of model name. Falls back to per-vendor clients (OpenAI / DeepSeek
direct / Anthropic SDK) only when no OpenRouter key is available.

Model name normalization
    OpenRouter expects ``vendor/model`` (e.g. ``deepseek/deepseek-chat``).
    Legacy ``.env`` files often use bare names like ``deepseek-chat`` or
    ``claude-sonnet-4-20250514``. ``_normalize_model()`` rewrites those into
    OpenRouter form so users don't have to edit ``.env`` to get going.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    content: str
    token_count: int
    model: str


# ---------------------------------------------------------------------------
# OpenRouter model name normalization
# ---------------------------------------------------------------------------

# Bare-name → OpenRouter id. Only covers names we ship in defaults / examples;
# anything else is left untouched (and can already be in vendor/model form).
_OPENROUTER_MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-coder": "deepseek/deepseek-coder",
    "deepseek-v3": "deepseek/deepseek-chat",
    "claude-sonnet-4": "anthropic/claude-sonnet-4",
    "claude-sonnet-4-20250514": "anthropic/claude-sonnet-4",
    "claude-opus-4": "anthropic/claude-opus-4",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
}


def _normalize_model(model: str) -> str:
    """Coerce a model name into OpenRouter ``vendor/model`` form when needed."""
    if "/" in model:  # already vendor-qualified
        return model
    return _OPENROUTER_MODEL_ALIASES.get(model, model)


# ---------------------------------------------------------------------------
# Client resolution
# ---------------------------------------------------------------------------


def _make_openai_client(api_key: str, base_url: str | None = None):
    from openai import OpenAI  # local import keeps test imports cheap

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _resolve_client(model: str) -> tuple[Any, str]:
    """Pick a client and (possibly normalized) model name.

    Returns ``(client, effective_model_name)``.
    """
    # 1. OpenRouter takes precedence whenever its key is present
    if settings.openrouter_api_key:
        client = _make_openai_client(
            settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
        return client, _normalize_model(model)

    # 2. Per-vendor fallbacks (legacy path)
    name = model.lower()
    if "deepseek" in name:
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
        return (
            _make_openai_client(
                settings.deepseek_api_key, base_url="https://api.deepseek.com"
            ),
            model,
        )
    if "claude" in name or "anthropic" in name:
        return _AnthropicAdapter(), model
    if not settings.openai_api_key:
        raise RuntimeError(
            "No LLM provider configured. Set OPENROUTER_API_KEY or one of "
            "OPENAI_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY."
        )
    return _make_openai_client(settings.openai_api_key), model


# ---------------------------------------------------------------------------
# Anthropic SDK adapter (only used when OpenRouter is not configured)
# ---------------------------------------------------------------------------


class _AnthropicAdapter:
    """Tiny adapter so the rest of the code can call ``.chat.completions.create()``."""

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
        from anthropic import Anthropic  # local import

        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ):
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        user_parts = [m for m in messages if m["role"] != "system"]
        resp = self._client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=temperature,
            system="\n\n".join(system_parts) if system_parts else None,
            messages=[
                {"role": m["role"], "content": m["content"]} for m in user_parts
            ],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = getattr(resp, "usage", None)
        in_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        out_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        return _AnthropicResponse(text, in_tokens + out_tokens)


@dataclass
class _AnthropicResponse:
    _text: str
    _tokens: int

    @property
    def choices(self) -> list[Any]:
        return [_AnthropicChoice(self._text)]

    @property
    def usage(self):
        return _AnthropicUsage(self._tokens)


@dataclass
class _AnthropicChoice:
    _text: str

    @property
    def message(self):
        return _AnthropicMessage(self._text)


@dataclass
class _AnthropicMessage:
    content: str


@dataclass
class _AnthropicUsage:
    total_tokens: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chat_complete(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
) -> ChatResult:
    """Run a chat completion and return text + best-effort token count."""
    client, effective_model = _resolve_client(model)

    if isinstance(client, _AnthropicAdapter):
        resp = client.create(
            model=effective_model, messages=messages, temperature=temperature
        )
        return ChatResult(
            content=resp.choices[0].message.content,
            token_count=resp.usage.total_tokens,
            model=effective_model,
        )

    resp = client.chat.completions.create(
        model=effective_model,
        messages=messages,
        temperature=temperature,
    )
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    token_count = getattr(usage, "total_tokens", 0) if usage else 0
    return ChatResult(content=content, token_count=token_count, model=effective_model)
