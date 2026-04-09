"""Unit tests for the LangGraph agent layer.

What we cover here (without touching a real DB or LLM):

* The SQL safety filter — known good queries pass, known bad queries are
  rejected by ``error_type='safety'``.
* The model router — every PRD §5.5 branch returns the expected model name.
* Per-node behavior with stubs:
    - intent_classifier falls back to the heuristic when the LLM raises
    - sql_executor turns ExecutionResult into the right state delta
    - self_correction increments the retry counter and appends to history
    - result_formatter infers visualization shape correctly
* End-to-end graph wiring with every node monkey-patched, exercising the
  success path and the retry-then-give-up path.

Run with::

    .venv/bin/python -m pytest tests/test_agent.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.agent import graph as graph_module
from src.agent.nodes import (
    intent_classifier as ic_module,
    result_formatter as rf_module,
    self_correction as sc_module,
    sql_executor as ex_module,
)
from src.config import settings
from src.connectors.base import QueryResult
from src.connectors.registry import ConnectorRegistry
from src.db.executor import ExecutionResult, _is_select_only, execute_sql
from src.models.state import make_initial_state
from src.router.model_router import estimate_complexity, route_model


# ---------------------------------------------------------------------------
# SQL safety filter
# ---------------------------------------------------------------------------


class TestSqlSafety:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "select * from dwd_order_detail",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "  SELECT count(*) FROM dwd_user_profile  ",
        ],
    )
    def test_select_passes(self, sql):
        ok, reason = _is_select_only(sql)
        assert ok, f"expected pass, got reason={reason!r}"

    @pytest.mark.parametrize(
        "sql,fragment",
        [
            ("DROP TABLE users", "DROP"),
            ("DELETE FROM users", "DELETE"),
            ("INSERT INTO foo VALUES (1)", "INSERT"),
            ("UPDATE users SET x=1", "UPDATE"),
            ("TRUNCATE foo", "TRUNCATE"),
            ("ALTER TABLE foo DROP COLUMN bar", "ALTER"),
            ("CREATE TABLE foo (id int)", "CREATE"),
            ("", "empty"),
            # Two statements masquerading as one — the forbidden-keyword
            # scan catches DROP before the multi-statement check fires, so
            # we just assert it's rejected for *some* reason.
            ("SELECT 1; DROP TABLE users", "drop"),
        ],
    )
    def test_dangerous_rejected(self, sql, fragment):
        ok, reason = _is_select_only(sql)
        assert not ok
        assert fragment.lower() in (reason or "").lower()

    def test_string_literal_with_dangerous_word_is_allowed(self):
        # The literal looks dangerous, but it's data — the filter strips
        # string literals before scanning.
        ok, _ = _is_select_only("SELECT 'DROP TABLE users' AS msg")
        assert ok

    def test_executor_returns_safety_error_without_db(self):
        # No DB needed: the safety check rejects before connecting.
        # 'DROP TABLE users' starts with DROP, so the head-statement check
        # fires first ("only SELECT/WITH statements allowed").
        result = execute_sql("DROP TABLE users")
        assert isinstance(result, ExecutionResult)
        assert result.success is False
        assert result.error_type == "safety"
        assert "select" in (result.error or "").lower()

    def test_executor_rejects_inline_dml_after_select(self):
        # 'SELECT 1' passes the head check but the forbidden-keyword scan
        # catches the DROP later in the string.
        result = execute_sql("SELECT 1 UNION SELECT 1; DROP TABLE users")
        assert result.success is False
        assert result.error_type == "safety"


# ---------------------------------------------------------------------------
# Model router
# ---------------------------------------------------------------------------


def _schemas(*names: str) -> list[dict[str, Any]]:
    return [{"table": n} for n in names]


class TestModelRouter:
    def test_simple_query_single_table_uses_cheap(self):
        assert route_model("simple_query", _schemas("dwd_user_profile")) == settings.default_cheap_model

    def test_aggregation_two_tables_uses_cheap(self):
        assert (
            route_model("aggregation", _schemas("dwd_order_detail", "dwd_user_profile"))
            == settings.default_cheap_model
        )

    def test_multi_join_uses_strong(self):
        assert route_model("multi_join", _schemas("a", "b")) == settings.default_strong_model

    def test_three_or_more_tables_uses_strong(self):
        assert (
            route_model("simple_query", _schemas("a", "b", "c"))
            == settings.default_strong_model
        )

    def test_exploration_uses_strong(self):
        assert route_model("exploration", _schemas("a")) == settings.default_strong_model

    def test_retry_count_2_forces_strong(self):
        # PRD §5.5 Phase-2 fallback — even on a simple query, after 2 failures
        # we should escalate to the strong model.
        assert (
            route_model("simple_query", _schemas("a"), retry_count=2)
            == settings.default_strong_model
        )

    def test_complexity_score_ranges(self):
        assert estimate_complexity("simple_query", _schemas("a")) == 1
        assert estimate_complexity("aggregation", _schemas("a", "b")) == 2
        assert estimate_complexity("multi_join", _schemas("a", "b", "c")) == 5
        assert estimate_complexity("exploration", _schemas()) == 4


# ---------------------------------------------------------------------------
# Individual node behavior
# ---------------------------------------------------------------------------


class _LLMRaisesError(Exception):
    pass


class TestIntentClassifierNode:
    def test_uses_heuristic_when_llm_unavailable(self, monkeypatch):
        def boom(*args, **kwargs):
            raise _LLMRaisesError("no api key")

        monkeypatch.setattr(ic_module, "chat_complete", boom)
        state = make_initial_state(user_query="按一级品类统计总销售额")
        out = ic_module.classify_intent_node(state)
        # "总" is in the heuristic's aggregation keyword set
        assert out["intent"] == "aggregation"
        assert out["clarification_question"] is None

    def test_heuristic_simple_query_default(self, monkeypatch):
        monkeypatch.setattr(
            ic_module,
            "chat_complete",
            lambda *a, **k: (_ for _ in ()).throw(_LLMRaisesError("offline")),
        )
        state = make_initial_state(user_query="给我看一下 user_id=42 的信息")
        out = ic_module.classify_intent_node(state)
        assert out["intent"] == "simple_query"


class _StubConnector:
    """Minimal DataSourceConnector stand-in for sql_executor_node tests."""

    def __init__(self, canned: QueryResult):
        self.name = "stub"
        self.dialect = "postgresql"
        self._canned = canned

    def get_dialect(self) -> str:
        return self.dialect

    async def execute_query(self, sql: str, timeout_seconds: int = 30, max_rows: int = 1000):
        return self._canned


@pytest.fixture
def stub_registry(monkeypatch):
    """Replace the singleton ConnectorRegistry with a tiny in-memory one.

    Yields a setter callback so tests can swap in different stub connectors
    per case without rebuilding the fixture.
    """
    ConnectorRegistry.reset_instance()
    registry = ConnectorRegistry.get_instance()

    def set_default(connector):
        registry._connectors["stub"] = connector  # noqa: SLF001
        registry._default_source = "stub"
        registry._initialized = True

    yield set_default
    ConnectorRegistry.reset_instance()


class TestSqlExecutorNode:
    def test_no_sql_short_circuits(self):
        state = make_initial_state(user_query="x")
        state["generated_sql"] = ""
        out = asyncio.run(ex_module.execute_sql_node(state))
        assert out["execution_success"] is False
        assert "no sql" in out["execution_error"].lower()
        assert out["execution_result"] is None

    def test_calls_executor_and_translates_success(self, stub_registry):
        canned = QueryResult(
            success=True,
            columns=["a"],
            rows=[{"a": 1}, {"a": 2}],
            row_count=2,
            execution_time_ms=5,
            sql_executed="SELECT a FROM t",
        )
        stub_registry(_StubConnector(canned))
        state = make_initial_state(user_query="x", active_source="stub")
        state["generated_sql"] = "SELECT a FROM t"
        out = asyncio.run(ex_module.execute_sql_node(state))
        assert out["execution_success"] is True
        assert out["row_count"] == 2
        assert out["execution_result"] == [{"a": 1}, {"a": 2}]

    def test_translates_failure_with_error(self, stub_registry):
        canned = QueryResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=5,
            sql_executed="SELECT bogus",
            error="syntax error at line 1",
            error_type="syntax",
        )
        stub_registry(_StubConnector(canned))
        state = make_initial_state(user_query="x", active_source="stub")
        state["generated_sql"] = "SELECT bogus"
        out = asyncio.run(ex_module.execute_sql_node(state))
        assert out["execution_success"] is False
        assert "syntax" in out["execution_error"]
        assert out["execution_result"] is None


class TestSelfCorrectionNode:
    def test_appends_to_history_and_bumps_counter(self):
        state = make_initial_state(user_query="x")
        state["generated_sql"] = "SELECT bogus"
        state["execution_error"] = "syntax error"
        state["correction_history"] = []
        state["retry_count"] = 0

        out = sc_module.self_correction_node(state)
        assert out["retry_count"] == 1
        assert len(out["correction_history"]) == 1
        last = out["correction_history"][-1]
        assert last["sql"] == "SELECT bogus"
        assert last["error"] == "syntax error"

    def test_preserves_existing_history(self):
        state = make_initial_state(user_query="x")
        state["generated_sql"] = "SELECT 2"
        state["execution_error"] = "second error"
        state["correction_history"] = [{"sql": "SELECT 1", "error": "first error", "feedback": ""}]
        state["retry_count"] = 1

        out = sc_module.self_correction_node(state)
        assert out["retry_count"] == 2
        assert len(out["correction_history"]) == 2
        assert out["correction_history"][0]["sql"] == "SELECT 1"
        assert out["correction_history"][1]["sql"] == "SELECT 2"


class TestResultFormatterNode:
    def test_single_value_returns_number_hint(self):
        state = make_initial_state(user_query="总用户数")
        state["execution_result"] = [{"count": 1234}]
        state["row_count"] = 1
        out = rf_module.format_result_node(state)
        assert out["visualization_hint"] == "number"
        assert "count" in out["final_answer"] or "1234" in out["final_answer"]

    def test_two_columns_with_date_returns_line_chart(self):
        state = make_initial_state(user_query="近 7 天订单数")
        state["execution_result"] = [
            {"order_date": "2026-04-01", "cnt": 12},
            {"order_date": "2026-04-02", "cnt": 9},
        ]
        state["row_count"] = 2
        out = rf_module.format_result_node(state)
        assert out["visualization_hint"] == "line_chart"

    def test_two_columns_categorical_returns_bar_chart(self):
        state = make_initial_state(user_query="按品类统计")
        state["execution_result"] = [
            {"category_l1": "电子", "total": 100},
            {"category_l1": "服装", "total": 80},
        ]
        state["row_count"] = 2
        out = rf_module.format_result_node(state)
        assert out["visualization_hint"] == "bar_chart"

    def test_more_than_two_columns_returns_table(self):
        state = make_initial_state(user_query="x")
        state["execution_result"] = [
            {"a": 1, "b": 2, "c": 3},
        ]
        state["row_count"] = 1
        out = rf_module.format_result_node(state)
        assert out["visualization_hint"] == "table"

    def test_empty_result_falls_back_to_table(self):
        state = make_initial_state(user_query="x")
        state["execution_result"] = []
        state["row_count"] = 0
        out = rf_module.format_result_node(state)
        assert out["visualization_hint"] == "table"
        assert "没有命中" in out["final_answer"]

    def test_error_branch_includes_failed_sql(self):
        state = make_initial_state(user_query="x")
        state["execution_error"] = "boom"
        state["generated_sql"] = "SELECT bad"
        state["retry_count"] = 3
        out = rf_module.format_error_node(state)
        assert "SELECT bad" in out["final_answer"]
        assert "boom" in out["final_answer"]
        assert out["visualization_hint"] is None

    def test_clarification_branch(self):
        state = make_initial_state(user_query="?")
        state["clarification_question"] = "请指定时间范围"
        out = rf_module.format_clarification_node(state)
        assert "请指定时间范围" in out["final_answer"]


# ---------------------------------------------------------------------------
# End-to-end graph wiring
# ---------------------------------------------------------------------------


def _patch_all_nodes(monkeypatch, *, intent="aggregation", exec_results):
    """Replace each node with a deterministic stub.

    ``exec_results`` is a list — each call to execute_sql_node pops the next
    one. Pass [success_result] for the happy path, or [fail, fail, fail] to
    exercise the retry-and-give-up branch.
    """
    calls = {"execute": 0}

    def fake_intent(state):
        return {"intent": intent}

    def fake_retrieve(state):
        return {"retrieved_schemas": [{"table": "dwd_order_detail"}]}

    def fake_generate(state):
        return {
            "generated_sql": f"SELECT 1  -- attempt {state.get('retry_count', 0)}",
            "model_used": "fake-model",
            "complexity_score": 2,
        }

    def fake_execute(state):
        i = calls["execute"]
        calls["execute"] += 1
        if i >= len(exec_results):
            i = len(exec_results) - 1
        result = exec_results[i]
        return {
            "execution_success": result.success,
            "execution_result": result.rows if result.success else None,
            "execution_error": result.error,
            "row_count": result.row_count,
        }

    def fake_self_correction(state):
        history = list(state.get("correction_history", []))
        history.append(
            {
                "sql": state.get("generated_sql", ""),
                "error": state.get("execution_error", "") or "",
                "feedback": "",
            }
        )
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "correction_history": history,
        }

    monkeypatch.setattr(graph_module, "classify_intent_node", fake_intent)
    monkeypatch.setattr(graph_module, "retrieve_schema_node", fake_retrieve)
    monkeypatch.setattr(graph_module, "generate_sql_node", fake_generate)
    monkeypatch.setattr(graph_module, "execute_sql_node", fake_execute)
    monkeypatch.setattr(graph_module, "self_correction_node", fake_self_correction)
    return calls


class TestGraphE2E:
    def test_success_path(self, monkeypatch):
        calls = _patch_all_nodes(
            monkeypatch,
            intent="aggregation",
            exec_results=[
                ExecutionResult(success=True, rows=[{"a": 1}], row_count=1, error=None),
            ],
        )
        # Rebuild with the patched nodes captured
        compiled = graph_module.build_agent_graph()
        state = make_initial_state(user_query="按品类统计销售额")
        final = compiled.invoke(state)
        assert final["execution_success"] is True
        assert final["row_count"] == 1
        assert final["visualization_hint"] in ("number", "bar_chart", "line_chart", "table")
        assert calls["execute"] == 1

    def test_retry_then_success(self, monkeypatch):
        calls = _patch_all_nodes(
            monkeypatch,
            intent="aggregation",
            exec_results=[
                ExecutionResult(success=False, rows=[], row_count=0, error="syntax error", error_type="syntax"),
                ExecutionResult(success=True, rows=[{"v": 42}], row_count=1, error=None),
            ],
        )
        compiled = graph_module.build_agent_graph()
        final = compiled.invoke(make_initial_state(user_query="x"))
        assert final["execution_success"] is True
        assert final["retry_count"] == 1
        assert calls["execute"] == 2
        assert len(final["correction_history"]) == 1

    def test_retry_exhaustion_routes_to_format_error(self, monkeypatch):
        # Three failures in a row, then we should give up — max_retry_count
        # defaults to 3 in settings, so we expect 4 execute calls total
        # (initial + 3 retries) before bailing.
        max_retries = settings.max_retry_count
        fails = [
            ExecutionResult(
                success=False,
                rows=[],
                row_count=0,
                error=f"err {i}",
                error_type="syntax",
            )
            for i in range(max_retries + 1)
        ]
        calls = _patch_all_nodes(monkeypatch, intent="multi_join", exec_results=fails)
        compiled = graph_module.build_agent_graph()
        final = compiled.invoke(make_initial_state(user_query="x"))
        assert final["execution_success"] is False
        assert final["visualization_hint"] is None
        # Final answer should contain the error narrative produced by format_error_node
        assert "重试" in final["final_answer"]
        assert calls["execute"] == max_retries + 1
        assert final["retry_count"] == max_retries

    def test_clarification_short_circuits(self, monkeypatch):
        calls = _patch_all_nodes(
            monkeypatch,
            intent="clarification",
            exec_results=[
                ExecutionResult(success=True, rows=[], row_count=0, error=None),
            ],
        )
        compiled = graph_module.build_agent_graph()
        state = make_initial_state(user_query="?")
        state["clarification_question"] = "请指定时间范围"
        # Need to inject the clarification question — the stub intent node
        # only sets intent. Patch it again for this test.
        def fake_intent(state):
            return {"intent": "clarification", "clarification_question": "请指定时间范围"}

        monkeypatch.setattr(graph_module, "classify_intent_node", fake_intent)
        compiled = graph_module.build_agent_graph()
        final = compiled.invoke(state)
        # Should never have hit execute_sql
        assert calls["execute"] == 0
        assert "请指定时间范围" in final["final_answer"]
