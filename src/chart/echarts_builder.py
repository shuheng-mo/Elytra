"""Build ECharts-compatible JSON option objects (Phase 2+).

The output is a plain dict that a frontend can pass directly to
``echarts.setOption()`` or the ``streamlit-echarts`` component.
"""

from __future__ import annotations

from typing import Any

# Maximum data points per chart to keep the response size reasonable.
_MAX_BAR_ITEMS = 20
_MAX_LINE_POINTS = 200
_MAX_PIE_SLICES = 10


def build_chart_spec(
    chart_type: str,
    rows: list[dict[str, Any]],
    title: str = "",
) -> dict[str, Any] | None:
    """Build an ECharts option dict for the given chart type and data.

    Returns ``None`` if the data cannot be rendered as the requested type.
    """
    if not rows or chart_type is None:
        return None

    cols = list(rows[0].keys())

    builders = {
        "number_card": _build_number_card,
        "bar": _build_bar,
        "line": _build_line,
        "pie": _build_pie,
        "scatter": _build_scatter,
        "multi_line": _build_multi_line,
    }

    builder = builders.get(chart_type)
    if builder is None:
        return None

    return builder(rows, cols, title)


def _build_number_card(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    col = cols[0]
    value = rows[0][col]
    return {
        "chart_type": "number_card",
        "title": title or col,
        "value": value,
        "field": col,
    }


def _build_bar(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    x_field, y_field = cols[0], cols[1]
    data = rows[:_MAX_BAR_ITEMS]
    return {
        "chart_type": "bar",
        "title": title,
        "x_axis": {"field": x_field, "data": [r[x_field] for r in data]},
        "y_axis": {"field": y_field},
        "series": [{"type": "bar", "data": [r[y_field] for r in data]}],
        "data": data,
    }


def _build_line(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    x_field, y_field = cols[0], cols[1]
    data = rows[:_MAX_LINE_POINTS]
    return {
        "chart_type": "line",
        "title": title,
        "x_axis": {"field": x_field, "data": [str(r[x_field]) for r in data]},
        "y_axis": {"field": y_field},
        "series": [{"type": "line", "data": [r[y_field] for r in data]}],
        "data": data,
    }


def _build_pie(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    name_field, value_field = cols[0], cols[1]
    data = rows[:_MAX_PIE_SLICES]
    return {
        "chart_type": "pie",
        "title": title,
        "series": [{
            "type": "pie",
            "data": [
                {"name": str(r[name_field]), "value": r[value_field]}
                for r in data
            ],
        }],
        "data": data,
    }


def _build_scatter(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    x_field, y_field = cols[0], cols[1]
    data = rows[:_MAX_LINE_POINTS]
    return {
        "chart_type": "scatter",
        "title": title,
        "x_axis": {"field": x_field},
        "y_axis": {"field": y_field},
        "series": [{"type": "scatter", "data": [[r[x_field], r[y_field]] for r in data]}],
        "data": data,
    }


def _build_multi_line(
    rows: list[dict[str, Any]], cols: list[str], title: str,
) -> dict[str, Any]:
    time_field, cat_field, val_field = cols[0], cols[1], cols[2]
    data = rows[:_MAX_LINE_POINTS]

    # Group by category
    categories: dict[str, list] = {}
    x_values: list[str] = []
    for r in data:
        cat = str(r[cat_field])
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r[val_field])
        x_val = str(r[time_field])
        if x_val not in x_values:
            x_values.append(x_val)

    series = [
        {"type": "line", "name": cat, "data": vals}
        for cat, vals in categories.items()
    ]

    return {
        "chart_type": "multi_line",
        "title": title,
        "x_axis": {"field": time_field, "data": x_values},
        "y_axis": {"field": val_field},
        "series": series,
        "data": data,
    }
