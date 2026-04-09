"""Global configuration loaded from environment variables.

All settings are read from the process environment (with `.env` support via
`python-dotenv`). Defaults mirror `.env.example` so the package can be imported
without a fully configured environment during tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _detect_openrouter_key() -> str:
    """Locate an OpenRouter API key.

    Preference order:
        1. ``OPENROUTER_API_KEY`` (canonical)
        2. Any of the per-vendor key slots whose value starts with ``sk-or-``
           (so the user can drop an OpenRouter key into ``DEEPSEEK_API_KEY``
           etc. without confusing the rest of the stack).
    """
    explicit = os.getenv("OPENROUTER_API_KEY", "")
    if explicit:
        return explicit
    for slot in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        val = os.getenv(slot, "")
        if val.startswith("sk-or-"):
            return val
    return ""


@dataclass(frozen=True)
class Settings:
    # LLM API keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openrouter_api_key: str = _detect_openrouter_key()
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://Elytra:Elytra_dev@localhost:5432/Elytra",
    )

    # Models — when OpenRouter is in use, names should be ``vendor/model``
    # (e.g. ``deepseek/deepseek-chat``). The LLM helper auto-prefixes
    # vendor-less names so legacy ``.env`` files keep working.
    default_cheap_model: str = os.getenv("DEFAULT_CHEAP_MODEL", "deepseek/deepseek-chat")
    default_strong_model: str = os.getenv(
        "DEFAULT_STRONG_MODEL", "anthropic/claude-sonnet-4"
    )
    # Embedding — provider is auto-detected from the model name unless
    # EMBEDDING_PROVIDER is set explicitly. Dim is auto-derived from a known-
    # model lookup table; only set EMBEDDING_DIM to override.
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-large")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "auto")
    embedding_dim: int = _get_int("EMBEDDING_DIM", 0)  # 0 = auto-detect

    # Retrieval
    bm25_weight: float = _get_float("BM25_WEIGHT", 0.4)
    vector_weight: float = _get_float("VECTOR_WEIGHT", 0.6)
    rerank_top_k: int = _get_int("RERANK_TOP_K", 5)
    max_retry_count: int = _get_int("MAX_RETRY_COUNT", 3)
    sql_timeout_seconds: int = _get_int("SQL_TIMEOUT_SECONDS", 30)

    # Paths
    data_dictionary_path: Path = PROJECT_ROOT / "db" / "data_dictionary.yaml"
    datasources_yaml_path: Path = PROJECT_ROOT / "config" / "datasources.yaml"

    # Default analytics data source — used when /api/query omits `source`.
    # Empty string means "fall back to whatever default_source is in the YAML".
    default_source: str = os.getenv("DEFAULT_SOURCE", "")


settings = Settings()
