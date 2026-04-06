"""Result formatting node.

Two roles:

1. **Success path** — turns the rows into a short natural-language answer +
   a visualization hint (number / table / bar_chart / line_chart) based on
   simple shape heuristics. The hint is used by the Streamlit frontend.
2. **Error path** — turns the final error (after all retries exhausted) into
   a user-facing message that includes the failed SQL.

We deliberately do *not* call the LLM here in Phase 1: the heuristics are
good enough for a 7-day MVP and they keep latency + cost down. Phase 2 may
swap this for an LLM-based summarizer.
"""

from __future__ import annotations

from src.models.state import AgentState


_DATE_KEYS = ("date", "day", "month", "week", "year", "time", "hour")


def _looks_like_date(col: str) -> bool:
    c = col.lower()
    return any(k in c for k in _DATE_KEYS)


def _infer_visualization(rows: list[dict]) -> str:
    if not rows:
        return "table"
    if len(rows) == 1 and len(rows[0]) == 1:
        return "number"

    cols = list(rows[0].keys())
    if len(cols) == 2:
        first, second = cols
        first_is_date = _looks_like_date(first)
        # numeric second column?
        sample = rows[0][second]
        is_numeric = isinstance(sample, (int, float))
        if first_is_date and is_numeric:
            return "line_chart"
        if is_numeric:
            return "bar_chart"

    return "table"


def _summarize_rows(user_query: str, rows: list[dict], row_count: int) -> str:
    if row_count == 0:
        return "查询执行成功，但没有命中任何数据。"
    if row_count == 1 and len(rows[0]) == 1:
        ((k, v),) = rows[0].items()
        return f"查询结果：{k} = {v}"
    return f"查询执行成功，共返回 {row_count} 行结果。"


def format_result_node(state: AgentState) -> dict:
    """LangGraph node: terminal node on the success branch."""
    rows = state.get("execution_result") or []
    row_count = state.get("row_count", len(rows))
    user_query = state.get("user_query", "")

    return {
        "final_answer": _summarize_rows(user_query, rows, row_count),
        "visualization_hint": _infer_visualization(rows),
    }


def format_error_node(state: AgentState) -> dict:
    """LangGraph node: terminal node when self-correction has been exhausted."""
    error = state.get("execution_error") or "(no error message captured)"
    sql = state.get("generated_sql") or ""
    retries = state.get("retry_count", 0)
    answer = (
        f"抱歉，未能成功生成可执行的 SQL（已重试 {retries} 次）。\n"
        f"最后一次错误：{error}\n"
        f"最后一次 SQL：\n{sql}"
    )
    return {
        "final_answer": answer,
        "visualization_hint": None,
    }


def format_clarification_node(state: AgentState) -> dict:
    """LangGraph node: terminal node on the clarification branch."""
    question = state.get("clarification_question") or "请补充更具体的查询条件，例如时间范围或目标指标。"
    return {
        "final_answer": f"需要更多信息：{question}",
        "visualization_hint": None,
    }
