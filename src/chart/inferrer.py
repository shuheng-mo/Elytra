"""Rule-based chart type inference (Phase 2+).

Examines the shape and column names/types of query results to recommend
the best chart type. Falls back to ``None`` (table display) when no rule
matches convincingly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

_DATE_KEYWORDS = ("date", "day", "month", "week", "year", "time", "hour", "quarter", "period")


def is_temporal(col_name: str) -> bool:
    """Heuristic: does the column name look like a date/time dimension?"""
    lower = col_name.lower()
    return any(k in lower for k in _DATE_KEYWORDS)


def is_numeric(value: Any) -> bool:
    """Check if a sample value is numeric (int, float, Decimal, or numeric string)."""
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False
    return False


def is_categorical(col_name: str, sample_value: Any) -> bool:
    """Heuristic: the column is likely categorical if it's a non-numeric string."""
    if not isinstance(sample_value, str):
        return False
    # Numeric strings (e.g. "1089392.72" from DECIMAL) are not categorical
    try:
        float(sample_value)
        return False
    except (ValueError, TypeError):
        return True


def infer_chart_type(rows: list[dict[str, Any]]) -> str | None:
    """Return a chart type string or ``None`` if the data is best shown as a table.

    Possible return values:
        ``"number_card"`` — single KPI value
        ``"line"``        — time-series trend
        ``"bar"``         — categorical comparison
        ``"pie"``         — proportional breakdown (≤ 8 slices)
        ``"scatter"``     — two numeric dimensions
        ``"multi_line"``  — multi-series time trend
        ``None``          — fall back to table display
    """
    if not rows:
        return None

    first_row = rows[0]
    cols = list(first_row.keys())
    n_cols = len(cols)
    n_rows = len(rows)

    # Single value → number card
    if n_rows == 1 and n_cols == 1:
        return "number_card"

    if n_cols == 2:
        c1, c2 = cols
        v1, v2 = first_row[c1], first_row[c2]

        # Temporal + numeric → line chart
        if is_temporal(c1) and is_numeric(v2):
            return "line"

        # Categorical + numeric, ≤ 8 rows → pie
        if is_categorical(c1, v1) and is_numeric(v2) and n_rows <= 8:
            return "pie"

        # Categorical + numeric → bar
        if is_categorical(c1, v1) and is_numeric(v2):
            return "bar"

        # Two numeric columns → scatter
        if is_numeric(v1) and is_numeric(v2):
            return "scatter"

    if n_cols == 3:
        c1, c2, c3 = cols
        v1, v2, v3 = first_row[c1], first_row[c2], first_row[c3]

        # Temporal + categorical + numeric → multi-line
        if is_temporal(c1) and is_categorical(c2, v2) and is_numeric(v3):
            return "multi_line"

    return None
