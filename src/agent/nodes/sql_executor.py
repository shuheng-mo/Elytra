"""SQL execution node.

Thin LangGraph wrapper around :func:`src.db.executor.execute_sql`. The
executor already handles SELECT-only safety filtering, statement timeout, and
error categorization, so this node is just adapter glue: pull the SQL out of
state, run it, write back the structured result.
"""

from __future__ import annotations

import logging

from src.db.executor import execute_sql
from src.models.state import AgentState

logger = logging.getLogger(__name__)


def execute_sql_node(state: AgentState) -> dict:
    """LangGraph node: writes ``execution_success``, ``execution_result``, etc."""
    sql = (state.get("generated_sql") or "").strip()
    if not sql:
        return {
            "execution_success": False,
            "execution_error": "no SQL was generated",
            "execution_result": None,
            "row_count": 0,
        }

    result = execute_sql(sql)
    if result.success:
        logger.info("SQL ok, %d rows", result.row_count)
    else:
        logger.info("SQL failed (%s): %s", result.error_type, result.error)

    return {
        "execution_success": result.success,
        "execution_result": result.rows if result.success else None,
        "execution_error": result.error,
        "row_count": result.row_count,
    }
