"""Legacy SQL execution shim — kept for backwards compatibility.

Phase 1's PG-only execution path lived here. Phase 2's connector layer
(`src/connectors/`) replaces it. This module now exists only to:

* Re-export ``_is_select_only`` so old test imports keep working until they
  migrate to ``src.connectors.base``.
* Provide a sync ``execute_sql()`` shim that dispatches to the registry's
  default PostgreSQL connector via ``asyncio.run``. The agent's hot path
  no longer calls this — ``src/agent/nodes/sql_executor.py`` talks to the
  registry directly. The shim is here for tooling and ad-hoc scripts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from src.config import settings
from src.connectors.base import _is_select_only  # noqa: F401  (re-export)


@dataclass
class ExecutionResult:
    """Legacy result type. New code should use
    :class:`src.connectors.base.QueryResult` instead.
    """

    success: bool
    rows: list[dict[str, Any]]
    row_count: int
    error: str | None = None
    error_type: str | None = None  # "safety" / "syntax" / "runtime" / "timeout"


def execute_sql(
    sql: str,
    *,
    timeout_seconds: int | None = None,
    max_rows: int = 1000,
) -> ExecutionResult:
    """Sync wrapper around the default connector. **Deprecated.**

    Used only by legacy callers (older scripts, ad-hoc REPL use, and a handful
    of unit tests). The agent's hot path uses the async connector registry
    directly via :func:`src.agent.nodes.sql_executor.execute_sql_node`.

    Safety filtering happens BEFORE we touch the registry, so callers can use
    this to assert "this SQL is unsafe" without standing up a database.
    """
    # Run the safety check first so unit tests can probe the filter without
    # needing a live registry / database.
    ok, reason = _is_select_only(sql)
    if not ok:
        return ExecutionResult(
            success=False,
            rows=[],
            row_count=0,
            error=f"SQL safety check failed: {reason}",
            error_type="safety",
        )

    from src.connectors.registry import ConnectorRegistry

    timeout_seconds = timeout_seconds or settings.sql_timeout_seconds
    registry = ConnectorRegistry.get_instance()

    async def _run() -> ExecutionResult:
        if not registry.is_initialized:
            await registry.init_from_yaml(settings.datasources_yaml_path)
        connector = registry.get()  # default source
        result = await connector.execute_query(
            sql, timeout_seconds=timeout_seconds, max_rows=max_rows
        )
        return ExecutionResult(
            success=result.success,
            rows=result.rows,
            row_count=result.row_count,
            error=result.error,
            error_type=result.error_type,
        )

    return asyncio.run(_run())
