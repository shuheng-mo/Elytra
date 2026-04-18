"""Unit tests for chart type inference and ECharts spec builder (Phase 2+)."""

from __future__ import annotations

from decimal import Decimal


from src.chart.inferrer import infer_chart_type, is_temporal, is_numeric, is_categorical
from src.chart.echarts_builder import build_chart_spec
from src.agent.nodes.chart_generator import generate_chart_node
from src.models.state import make_initial_state


# ---------------------------------------------------------------------------
# Inferrer helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_is_temporal(self):
        assert is_temporal("stat_date") is True
        assert is_temporal("order_month") is True
        assert is_temporal("week_start") is True
        assert is_temporal("category_l1") is False

    def test_is_numeric(self):
        assert is_numeric(42) is True
        assert is_numeric(3.14) is True
        assert is_numeric(Decimal("100.5")) is True
        assert is_numeric("hello") is False

    def test_is_categorical(self):
        assert is_categorical("name", "电子产品") is True
        assert is_categorical("count", 42) is False


# ---------------------------------------------------------------------------
# Chart type inference
# ---------------------------------------------------------------------------


class TestInferChartType:
    def test_empty_rows(self):
        assert infer_chart_type([]) is None

    def test_single_value_number_card(self):
        assert infer_chart_type([{"count": 1234}]) == "number_card"

    def test_temporal_numeric_line(self):
        rows = [
            {"stat_date": "2026-04-01", "total_amount": 1000},
            {"stat_date": "2026-04-02", "total_amount": 1200},
        ]
        assert infer_chart_type(rows) == "line"

    def test_categorical_numeric_few_rows_pie(self):
        rows = [
            {"category_l1": "电子", "total": 100},
            {"category_l1": "服装", "total": 80},
            {"category_l1": "食品", "total": 60},
        ]
        assert infer_chart_type(rows) == "pie"

    def test_categorical_numeric_many_rows_bar(self):
        rows = [{"category": f"cat_{i}", "value": i * 10} for i in range(15)]
        assert infer_chart_type(rows) == "bar"

    def test_two_numeric_scatter(self):
        rows = [
            {"price": 100, "quantity": 50},
            {"price": 200, "quantity": 30},
        ]
        assert infer_chart_type(rows) == "scatter"

    def test_temporal_categorical_numeric_multi_line(self):
        rows = [
            {"stat_date": "2026-04-01", "category": "A", "value": 10},
            {"stat_date": "2026-04-01", "category": "B", "value": 20},
            {"stat_date": "2026-04-02", "category": "A", "value": 15},
        ]
        assert infer_chart_type(rows) == "multi_line"

    def test_many_columns_returns_none(self):
        rows = [{"a": 1, "b": 2, "c": "x", "d": 4}]
        assert infer_chart_type(rows) is None

    def test_all_string_columns_returns_none(self):
        rows = [{"name": "Alice", "city": "Beijing"}]
        assert infer_chart_type(rows) is None


# ---------------------------------------------------------------------------
# ECharts spec builder
# ---------------------------------------------------------------------------


class TestBuildChartSpec:
    def test_bar_spec(self):
        rows = [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}]
        spec = build_chart_spec("bar", rows, title="test")
        assert spec is not None
        assert spec["chart_type"] == "bar"
        assert spec["x_axis"]["data"] == ["A", "B"]
        assert spec["series"][0]["type"] == "bar"
        assert spec["series"][0]["data"] == [10, 20]

    def test_line_spec(self):
        rows = [{"date": "2026-04-01", "v": 100}, {"date": "2026-04-02", "v": 200}]
        spec = build_chart_spec("line", rows)
        assert spec["chart_type"] == "line"
        assert len(spec["series"][0]["data"]) == 2

    def test_pie_spec(self):
        rows = [{"cat": "A", "val": 30}, {"cat": "B", "val": 70}]
        spec = build_chart_spec("pie", rows)
        assert spec["chart_type"] == "pie"
        assert len(spec["series"][0]["data"]) == 2
        assert spec["series"][0]["data"][0]["name"] == "A"

    def test_number_card_spec(self):
        rows = [{"total": 42}]
        spec = build_chart_spec("number_card", rows)
        assert spec["chart_type"] == "number_card"
        assert spec["value"] == 42

    def test_scatter_spec(self):
        rows = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        spec = build_chart_spec("scatter", rows)
        assert spec["chart_type"] == "scatter"
        assert spec["series"][0]["data"] == [[1, 2], [3, 4]]

    def test_multi_line_spec(self):
        rows = [
            {"date": "04-01", "series": "A", "val": 10},
            {"date": "04-01", "series": "B", "val": 20},
        ]
        spec = build_chart_spec("multi_line", rows)
        assert spec["chart_type"] == "multi_line"
        assert len(spec["series"]) == 2

    def test_unknown_type_returns_none(self):
        assert build_chart_spec("unknown", [{"a": 1}]) is None

    def test_empty_rows_returns_none(self):
        assert build_chart_spec("bar", []) is None

    def test_bar_truncates_to_max(self):
        rows = [{"cat": f"c{i}", "val": i} for i in range(50)]
        spec = build_chart_spec("bar", rows)
        assert len(spec["series"][0]["data"]) == 20


# ---------------------------------------------------------------------------
# Chart generator node
# ---------------------------------------------------------------------------


class TestChartGeneratorNode:
    def test_success_with_bar_data(self):
        state = make_initial_state(user_query="各品类销售额")
        state["execution_success"] = True
        state["execution_result"] = [
            {"category_l1": "电子", "total": 100},
            {"category_l1": "服装", "total": 80},
            {"category_l1": "食品", "total": 60},
            {"category_l1": "家居", "total": 40},
            {"category_l1": "运动", "total": 35},
            {"category_l1": "美妆", "total": 30},
            {"category_l1": "图书", "total": 25},
            {"category_l1": "其他", "total": 20},
            {"category_l1": "数码", "total": 15},  # 9 rows → bar (> 8)
        ]
        out = generate_chart_node(state)
        assert out["chart_spec"] is not None
        assert out["chart_spec"]["chart_type"] == "bar"

    def test_empty_result_returns_none(self):
        state = make_initial_state(user_query="x")
        state["execution_success"] = True
        state["execution_result"] = []
        out = generate_chart_node(state)
        assert out["chart_spec"] is None

    def test_failed_execution_returns_none(self):
        state = make_initial_state(user_query="x")
        state["execution_success"] = False
        state["execution_result"] = None
        out = generate_chart_node(state)
        assert out["chart_spec"] is None

    def test_table_data_returns_none(self):
        state = make_initial_state(user_query="x")
        state["execution_success"] = True
        state["execution_result"] = [
            {"a": 1, "b": "x", "c": True, "d": 4.0},
        ]
        out = generate_chart_node(state)
        assert out["chart_spec"] is None
