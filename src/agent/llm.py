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
    import httpx
    from openai import OpenAI  # local import keeps test imports cheap

    # The OpenAI SDK's default connect timeout is 5s, which is too tight for
    # cross-border links to OpenRouter (and any other internationally-hosted
    # API): when the TLS handshake stalls, every retry restarts the 5s clock,
    # so a sluggish ~10s handshake fails all retries before the read phase
    # ever starts. Read is generous because slow strong models (Claude Sonnet,
    # GPT-4o) routinely keep a request open for 60s+ before the first token.
    timeout = httpx.Timeout(connect=20.0, read=180.0, write=60.0, pool=60.0)

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    return OpenAI(api_key=api_key, timeout=timeout)


# Module-level client cache — reuses TCP/TLS connections across calls.
_client_cache: dict[str, tuple[Any, str]] = {}


def _resolve_client(model: str) -> tuple[Any, str]:
    """Pick a client and (possibly normalized) model name.

    Returns ``(client, effective_model_name)``.

    Clients are cached at module scope so TCP/TLS connections are reused
    across calls, saving ~1-2s per LLM request.

    Resolution priority (first match wins):
        1. ``ollama/*`` prefix + ``OLLAMA_BASE_URL`` set → local Ollama
        2. ``vllm/*``  prefix + ``VLLM_BASE_URL``   set → self-hosted vLLM
        3. ``OPENROUTER_API_KEY`` present → OpenRouter (any model)
        4. Per-vendor fallbacks (DeepSeek / Anthropic / OpenAI)
    """
    name = model.lower()

    # 1. Local / self-hosted backends — model-name prefix explicitly opts in.
    #    These win over OpenRouter so users who set a local backend can still
    #    keep an OpenRouter key in ``.env`` for other models.
    if name.startswith("ollama/") and settings.ollama_base_url:
        cache_key = "ollama"
        if cache_key not in _client_cache:
            client = _make_openai_client(
                api_key="ollama",  # Ollama ignores it; OpenAI SDK requires non-empty
                base_url=settings.ollama_base_url.rstrip("/") + "/v1",
            )
            _client_cache[cache_key] = (client, "")
        return _client_cache[cache_key][0], model.split("/", 1)[1]

    if name.startswith("vllm/") and settings.vllm_base_url:
        cache_key = "vllm"
        if cache_key not in _client_cache:
            client = _make_openai_client(
                api_key="vllm",
                base_url=settings.vllm_base_url.rstrip("/") + "/v1",
            )
            _client_cache[cache_key] = (client, "")
        return _client_cache[cache_key][0], model.split("/", 1)[1]

    # 2. OpenRouter takes precedence whenever its key is present
    if settings.openrouter_api_key:
        cache_key = "openrouter"
        if cache_key not in _client_cache:
            client = _make_openai_client(
                settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
            )
            _client_cache[cache_key] = (client, "")
        return _client_cache[cache_key][0], _normalize_model(model)

    # 3. Per-vendor fallbacks (legacy path) — reuses ``name`` from top of fn
    if "deepseek" in name:
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
        cache_key = "deepseek"
        if cache_key not in _client_cache:
            _client_cache[cache_key] = (
                _make_openai_client(
                    settings.deepseek_api_key, base_url="https://api.deepseek.com"
                ),
                model,
            )
        return _client_cache[cache_key][0], model
    if "claude" in name or "anthropic" in name:
        cache_key = "anthropic"
        if cache_key not in _client_cache:
            _client_cache[cache_key] = (_AnthropicAdapter(), model)
        return _client_cache[cache_key][0], model
    if not settings.openai_api_key:
        raise RuntimeError(
            "No LLM provider configured. Set OPENROUTER_API_KEY or one of "
            "OPENAI_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY."
        )
    cache_key = "openai"
    if cache_key not in _client_cache:
        _client_cache[cache_key] = (_make_openai_client(settings.openai_api_key), model)
    return _client_cache[cache_key][0], model


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
