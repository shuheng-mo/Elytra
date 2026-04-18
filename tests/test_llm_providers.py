"""Unit tests for ``src.agent.llm._resolve_client`` provider routing.

These tests cover the new Ollama / vLLM local-backend branches added in the
v0.6.x infrastructure patch, plus regression coverage for the existing
OpenRouter-first / per-vendor fallback paths.

No real network or LLM call is made: we replace ``_make_openai_client`` with
a stub that records its ``base_url`` / ``api_key`` arguments and returns a
bare object, then inspect the module-level ``_client_cache`` to verify the
correct branch fired.

Run with::

    .venv/bin/python -m pytest tests/test_llm_providers.py -v
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

import src.agent.llm as llm_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Minimal stand-in for the OpenAI client. We only care about identity."""

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """Ensure every test starts with an empty client cache so branch-specific
    cache keys (``ollama``, ``vllm``, ``openrouter``, ...) don't leak across
    tests."""
    llm_mod._client_cache.clear()
    yield
    llm_mod._client_cache.clear()


@pytest.fixture
def stub_factory(monkeypatch):
    """Replace ``_make_openai_client`` with a recorder that returns a stub.

    Returns the ``calls`` list so tests can inspect each invocation's kwargs.
    """
    calls: list[dict[str, Any]] = []

    def fake_make(api_key, base_url=None):
        calls.append({"api_key": api_key, "base_url": base_url})
        return _RecordingClient(base_url=base_url, api_key=api_key)

    monkeypatch.setattr(llm_mod, "_make_openai_client", fake_make)
    return calls


def _set_settings(monkeypatch, **overrides):
    """Patch ``llm_mod.settings`` with a frozen-dataclass copy that has the
    requested fields overridden. Defaults strip all API keys so we can test
    each branch in isolation without leaking real env state.

    ``overrides`` keys must match Settings field names exactly.
    """
    blank_defaults = {
        "openai_api_key": "",
        "deepseek_api_key": "",
        "anthropic_api_key": "",
        "openrouter_api_key": "",
        "ollama_base_url": "",
        "vllm_base_url": "",
    }
    blank_defaults.update(overrides)
    new_settings = replace(llm_mod.settings, **blank_defaults)
    monkeypatch.setattr(llm_mod, "settings", new_settings)


# ---------------------------------------------------------------------------
# Ollama branch
# ---------------------------------------------------------------------------


class TestOllamaRouting:
    def test_ollama_prefix_routes_to_ollama_cache(self, monkeypatch, stub_factory):
        _set_settings(monkeypatch, ollama_base_url="http://localhost:11434")
        client, effective = llm_mod._resolve_client("ollama/qwen2.5:7b")

        assert "ollama" in llm_mod._client_cache
        assert effective == "qwen2.5:7b"  # prefix stripped
        # exactly one factory call, with the /v1 suffix appended to the base URL
        assert len(stub_factory) == 1
        assert stub_factory[0]["base_url"] == "http://localhost:11434/v1"
        # sanity: returned client is the one we cached
        assert client is llm_mod._client_cache["ollama"][0]

    def test_ollama_effective_model_strips_prefix(self, monkeypatch, stub_factory):
        _set_settings(monkeypatch, ollama_base_url="http://localhost:11434")
        _, effective = llm_mod._resolve_client("ollama/nomic-embed-text")
        assert effective == "nomic-embed-text"
        assert "/" not in effective

    def test_ollama_base_url_trailing_slash_stripped(self, monkeypatch, stub_factory):
        _set_settings(monkeypatch, ollama_base_url="http://localhost:11434/")
        llm_mod._resolve_client("ollama/mistral")
        assert stub_factory[0]["base_url"] == "http://localhost:11434/v1"

    def test_ollama_prefix_without_base_url_falls_through(self, monkeypatch, stub_factory):
        """When OLLAMA_BASE_URL is empty, ``ollama/x`` must not consume the
        request — it should fall through to OpenRouter / per-vendor fallbacks."""
        _set_settings(
            monkeypatch,
            ollama_base_url="",
            openrouter_api_key="sk-or-test",
        )
        client, effective = llm_mod._resolve_client("ollama/qwen2.5")

        # Should have hit OpenRouter, NOT ollama
        assert "ollama" not in llm_mod._client_cache
        assert "openrouter" in llm_mod._client_cache
        # OpenRouter keeps the full original name (no prefix-aliasing for
        # unknown vendors like ``ollama/*``)
        assert effective == "ollama/qwen2.5"


# ---------------------------------------------------------------------------
# vLLM branch
# ---------------------------------------------------------------------------


class TestVLLMRouting:
    def test_vllm_prefix_routes_to_vllm_cache(self, monkeypatch, stub_factory):
        _set_settings(monkeypatch, vllm_base_url="http://localhost:8000")
        client, effective = llm_mod._resolve_client("vllm/meta-llama/Llama-3.1-70B")

        assert "vllm" in llm_mod._client_cache
        # only the FIRST "/" is stripped — HF repo names keep their slash
        assert effective == "meta-llama/Llama-3.1-70B"
        assert stub_factory[0]["base_url"] == "http://localhost:8000/v1"

    def test_vllm_prefix_without_base_url_falls_through(self, monkeypatch, stub_factory):
        _set_settings(
            monkeypatch,
            vllm_base_url="",
            openrouter_api_key="sk-or-test",
        )
        llm_mod._resolve_client("vllm/anything")
        assert "vllm" not in llm_mod._client_cache
        assert "openrouter" in llm_mod._client_cache


# ---------------------------------------------------------------------------
# Co-existence + priority
# ---------------------------------------------------------------------------


class TestLocalBackendPriority:
    def test_ollama_and_vllm_coexist(self, monkeypatch, stub_factory):
        """When both base URLs are configured, each prefix routes to its
        own cache slot."""
        _set_settings(
            monkeypatch,
            ollama_base_url="http://localhost:11434",
            vllm_base_url="http://localhost:8000",
        )

        llm_mod._resolve_client("ollama/qwen2.5")
        llm_mod._resolve_client("vllm/llama3")

        assert "ollama" in llm_mod._client_cache
        assert "vllm" in llm_mod._client_cache
        assert llm_mod._client_cache["ollama"][0] is not llm_mod._client_cache["vllm"][0]

    def test_local_prefix_wins_over_openrouter(self, monkeypatch, stub_factory):
        """Even with an OpenRouter key present, an ollama/ prefix must route
        to Ollama — users who set both want the prefix to be authoritative."""
        _set_settings(
            monkeypatch,
            ollama_base_url="http://localhost:11434",
            openrouter_api_key="sk-or-some-valid-key",
        )
        llm_mod._resolve_client("ollama/qwen2.5")

        assert "ollama" in llm_mod._client_cache
        assert "openrouter" not in llm_mod._client_cache


class TestRegressionOtherBranches:
    def test_openrouter_still_wins_for_non_local_prefix(self, monkeypatch, stub_factory):
        """Regression: setting ollama/vllm base URLs must NOT change OpenRouter
        routing for non-local-prefix models."""
        _set_settings(
            monkeypatch,
            ollama_base_url="http://localhost:11434",
            vllm_base_url="http://localhost:8000",
            openrouter_api_key="sk-or-test",
        )
        _, effective = llm_mod._resolve_client("deepseek-chat")
        assert "openrouter" in llm_mod._client_cache
        # bare name is normalized to OpenRouter form
        assert effective == "deepseek/deepseek-chat"

    def test_cache_reuses_client_across_calls(self, monkeypatch, stub_factory):
        """Multiple calls to ``_resolve_client`` with the same prefix must
        reuse the same underlying client (TCP/TLS pooling)."""
        _set_settings(monkeypatch, ollama_base_url="http://localhost:11434")
        c1, _ = llm_mod._resolve_client("ollama/qwen2.5")
        c2, _ = llm_mod._resolve_client("ollama/mistral")
        assert c1 is c2
        # Factory called exactly once despite two resolves
        assert len(stub_factory) == 1
