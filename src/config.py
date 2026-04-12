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
    # v0.5.0 — reranker backend selection.
    #
    # Default is "none" — the hybrid retriever's BM25+vector score fusion
    # is used directly. This saves ~12s per query by skipping the LLM
    # rerank round-trip. For deployments where schema accuracy matters
    # more than latency, set RERANKER_PROVIDER=llm or =local.
    reranker_provider: str = os.getenv("RERANKER_PROVIDER", "none")
    reranker_model: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    # Column-level schema retrieval — weight for merging column hits into
    # their parent table score. Set to 0 to disable column-level retrieval.
    column_retrieval_weight: float = _get_float("COLUMN_RETRIEVAL_WEIGHT", 0.6)

    # Paths
    data_dictionary_path: Path = PROJECT_ROOT / "db" / "data_dictionary.yaml"
    datasources_yaml_path: Path = PROJECT_ROOT / "config" / "datasources.yaml"
    permissions_yaml_path: Path = PROJECT_ROOT / "config" / "permissions.yaml"

    # Async task manager
    max_concurrent_tasks: int = _get_int("MAX_CONCURRENT_TASKS", 5)

    # Default analytics data source — used when /api/query omits `source`.
    # Empty string means "fall back to whatever default_source is in the YAML".
    default_source: str = os.getenv("DEFAULT_SOURCE", "")


settings = Settings()


# ---------------------------------------------------------------------------
# Hot-reload support: admin can change env vars at runtime via the API.
# ---------------------------------------------------------------------------

# The subset of env vars exposed to the admin config API.
# Keys = env var name, values = (field_name_on_Settings, type, description).
CONFIGURABLE_VARS: dict[str, tuple[str, type, str]] = {
    "DEFAULT_CHEAP_MODEL": ("default_cheap_model", str, "轻量模型（意图分类、简单查询）"),
    "DEFAULT_STRONG_MODEL": ("default_strong_model", str, "强模型（复杂查询、多表 JOIN）"),
    "BM25_WEIGHT": ("bm25_weight", float, "BM25 检索权重"),
    "VECTOR_WEIGHT": ("vector_weight", float, "向量检索权重"),
    "RERANK_TOP_K": ("rerank_top_k", int, "Rerank 返回表数"),
    "MAX_RETRY_COUNT": ("max_retry_count", int, "SQL 自修正最大重试次数"),
    "SQL_TIMEOUT_SECONDS": ("sql_timeout_seconds", int, "SQL 执行超时（秒）"),
    "RERANKER_PROVIDER": ("reranker_provider", str, "Reranker 后端（none / llm / local / auto）"),
    "COLUMN_RETRIEVAL_WEIGHT": ("column_retrieval_weight", float, "列级检索权重"),
    "MAX_CONCURRENT_TASKS": ("max_concurrent_tasks", int, "最大并发异步任务数"),
    "INTENT_CLASSIFIER": ("", str, "意图分类器（heuristic / llm）"),
}


def reload_settings() -> None:
    """Re-read env vars and rebuild the global ``settings`` singleton.

    Dataclass field defaults are evaluated at class-definition time, so
    ``Settings()`` alone won't pick up runtime changes to ``os.environ``.
    We must pass the current values explicitly.
    """
    global settings  # noqa: PLW0603
    settings = Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        openrouter_api_key=_detect_openrouter_key(),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        database_url=os.getenv("DATABASE_URL", "postgresql://Elytra:Elytra_dev@localhost:5432/Elytra"),
        default_cheap_model=os.getenv("DEFAULT_CHEAP_MODEL", "deepseek/deepseek-chat"),
        default_strong_model=os.getenv("DEFAULT_STRONG_MODEL", "anthropic/claude-sonnet-4"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-large"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "auto"),
        embedding_dim=_get_int("EMBEDDING_DIM", 0),
        bm25_weight=_get_float("BM25_WEIGHT", 0.4),
        vector_weight=_get_float("VECTOR_WEIGHT", 0.6),
        rerank_top_k=_get_int("RERANK_TOP_K", 5),
        max_retry_count=_get_int("MAX_RETRY_COUNT", 3),
        sql_timeout_seconds=_get_int("SQL_TIMEOUT_SECONDS", 30),
        reranker_provider=os.getenv("RERANKER_PROVIDER", "none"),
        reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        column_retrieval_weight=_get_float("COLUMN_RETRIEVAL_WEIGHT", 0.6),
        max_concurrent_tasks=_get_int("MAX_CONCURRENT_TASKS", 5),
        default_source=os.getenv("DEFAULT_SOURCE", ""),
    )
