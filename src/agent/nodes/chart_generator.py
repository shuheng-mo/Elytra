"""LangGraph node: generate ECharts-compatible chart spec (Phase 2+).

Sits after ``format_result`` on the success path. Infers chart type from
result shape using a rule-based engine and builds an ECharts option dict.
Writes ``chart_spec`` (or ``None``) into AgentState.
"""

from __future__ import annotations

import logging

from src.chart.echarts_builder import build_chart_spec
from src.chart.inferrer import infer_chart_type
from src.models.state import AgentState

logger = logging.getLogger(__name__)


def generate_chart_node(state: AgentState) -> dict:
    """Infer chart type and build ECharts spec from query results."""
    rows = state.get("execution_result") or []

    if not rows or not state.get("execution_success"):
        return {"chart_spec": None}

    chart_type = infer_chart_type(rows)
    if chart_type is None:
        return {"chart_spec": None}

    spec = build_chart_spec(chart_type, rows, title=state.get("user_query", ""))
    return {"chart_spec": spec}
