"""SQL execution node — async, multi-source aware.

Routes to the connector identified by ``state['active_source']`` (falls back
to the registry's default source). The connector handles safety filtering,
timeout, and error categorization; this node is just the adapter glue between
LangGraph and the connector layer.

The shape of what we write back into ``AgentState`` is preserved from Phase 1
(``execution_success`` / ``execution_result`` / ``execution_error`` /
``row_count``) so the self-correction prompt path keeps working unchanged.
"""

from __future__ import annotations

import logging

from src.connectors.registry import ConnectorRegistry
from src.models.state import AgentState

logger = logging.getLogger(__name__)


async def execute_sql_node(state: AgentState) -> dict:
    """LangGraph async node: writes ``execution_*`` and ``row_count``."""
    sql = (state.get("generated_sql") or "").strip()
    if not sql:
        return {
            "execution_success": False,
            "execution_error": "no SQL was generated",
            "execution_result": None,
            "row_count": 0,
        }

    registry = ConnectorRegistry.get_instance()
    source_name = state.get("active_source") or None
    try:
        connector = registry.get(source_name)
    except KeyError as exc:
        logger.error("connector lookup failed: %s", exc)
        return {
            "execution_success": False,
            "execution_error": f"unknown data source: {exc}",
            "execution_result": None,
            "row_count": 0,
        }

    result = await connector.execute_query(sql)

    if result.success:
        logger.info(
            "SQL ok on %s, %d rows in %dms",
            connector.name,
            result.row_count,
            result.execution_time_ms,
        )
    else:
        logger.info(
            "SQL failed on %s (%s): %s",
            connector.name,
            result.error_type,
            result.error,
        )

    return {
        "execution_success": result.success,
        "execution_result": result.rows if result.success else None,
        "execution_error": result.error,
        "row_count": result.row_count,
    }
