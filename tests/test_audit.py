"""Unit tests for audit features: result hash, replay, and stats endpoints."""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.api import audit as audit_module
from src.api.query import _compute_result_hash
from src.connectors.registry import ConnectorRegistry
from src.main import app


# ---------------------------------------------------------------------------
# _compute_result_hash tests
# ---------------------------------------------------------------------------


class TestComputeResultHash:
    def test_deterministic(self):
        rows = [{"a": 1, "b": "hello"}, {"a": 2, "b": "world"}]
        h1 = _compute_result_hash(rows)
        h2 = _compute_result_hash(rows)
        assert h1 is not None
        assert h1 == h2

    def test_none_rows_returns_none(self):
        assert _compute_result_hash(None) is None

    def test_empty_rows_returns_none(self):
        assert _compute_result_hash([]) is None

    def test_different_data_different_hash(self):
        h1 = _compute_result_hash([{"x": 1}])
        h2 = _compute_result_hash([{"x": 2}])
        assert h1 != h2

    def test_handles_decimal_and_datetime(self):
        rows = [{"amount": Decimal("123.45"), "ts": dt.datetime(2026, 1, 1)}]
        h = _compute_result_hash(rows)
        assert h is not None
        assert len(h) == 64  # SHA-256 hex

    def test_key_order_does_not_matter(self):
        h1 = _compute_result_hash([{"a": 1, "b": 2}])
        h2 = _compute_result_hash([{"b": 2, "a": 1}])
        assert h1 == h2

    def test_truncates_to_100_rows(self):
        rows_200 = [{"i": i} for i in range(200)]
        rows_100 = [{"i": i} for i in range(100)]
        assert _compute_result_hash(rows_200) == _compute_result_hash(rows_100)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


class _StubConnector:
    def __init__(self, name="stub_pg", dialect="postgresql"):
        self.name = name
        self.dialect = dialect
        self._connected = True

    def get_dialect(self):
        return self.dialect

    @property
    def is_connected(self):
        return self._connected

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def test_connection(self):
        return True

    async def execute_query(self, sql, timeout_seconds=30, max_rows=1000):
        from src.connectors.base import QueryResult
        return QueryResult(success=True, columns=["x"], rows=[{"x": 1}],
                           row_count=1, execution_time_ms=1, sql_executed=sql)

    async def get_tables(self):
        from src.connectors.base import TableMeta
        return [TableMeta(table_name="stub_table", schema_name="public", columns=[])]


@pytest.fixture
def client() -> TestClient:
    from src.retrieval.schema_loader import SchemaLoader
    ConnectorRegistry.reset_instance()
    SchemaLoader.clear_cache()
    registry = ConnectorRegistry.get_instance()
    stub = _StubConnector()
    registry._connectors["stub_pg"] = stub
    registry._default_source = "stub_pg"
    registry._raw_configs = [{"name": "stub_pg", "dialect": "postgresql", "overlay": None}]
    registry._initialized = True
    yield TestClient(app)
    ConnectorRegistry.reset_instance()
    SchemaLoader.clear_cache()


# ---------------------------------------------------------------------------
# POST /api/replay/{history_id}
# ---------------------------------------------------------------------------


class TestReplayEndpoint:
    def test_not_found(self, client, monkeypatch):
        monkeypatch.setattr(audit_module, "_load_history_row", lambda hid: None)
        resp = client.post("/api/replay/999")
        assert resp.status_code == 404

    def test_replay_success_match(self, client, monkeypatch):
        original_hash = _compute_result_hash([{"x": 1}])

        def fake_load(hid):
            return {
                "id": hid,
                "session_id": "s1",
                "user_query": "test query",
                "intent": "simple_query",
                "generated_sql": "SELECT 1",
                "execution_success": True,
                "retry_count": 0,
                "model_used": "fake",
                "latency_ms": 100,
                "token_count": 50,
                "estimated_cost": None,
                "created_at": dt.datetime(2026, 4, 10),
                "user_id": None,
                "user_role": None,
                "source_name": "stub_pg",
                "result_row_count": 1,
                "result_hash": original_hash,
            }

        async def fake_agent(user_query, session_id="", sql_dialect="postgresql", active_source=""):
            return {
                "execution_success": True,
                "execution_result": [{"x": 1}],
                "generated_sql": "SELECT 1",
                "row_count": 1,
                "latency_ms": 50,
            }

        monkeypatch.setattr(audit_module, "_load_history_row", fake_load)
        monkeypatch.setattr(audit_module, "run_agent_async", fake_agent)

        resp = client.post("/api/replay/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["result_match"] is True
        assert body["diff_summary"] is None
        assert body["original"]["id"] == 1

    def test_replay_mismatch(self, client, monkeypatch):
        def fake_load(hid):
            return {
                "id": hid, "session_id": "s1", "user_query": "q",
                "intent": "simple_query", "generated_sql": "SELECT 1",
                "execution_success": True, "retry_count": 0,
                "model_used": "fake", "latency_ms": 100,
                "token_count": 50, "estimated_cost": None,
                "created_at": dt.datetime(2026, 4, 10),
                "user_id": None, "user_role": None,
                "source_name": "stub_pg",
                "result_row_count": 1,
                "result_hash": "aaaa" * 16,
            }

        async def fake_agent(**kw):
            return {
                "execution_success": True,
                "execution_result": [{"x": 999}],
                "generated_sql": "SELECT 999",
                "row_count": 1,
                "latency_ms": 50,
            }

        monkeypatch.setattr(audit_module, "_load_history_row", fake_load)
        monkeypatch.setattr(audit_module, "run_agent_async", fake_agent)

        resp = client.post("/api/replay/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["result_match"] is False
        assert body["diff_summary"] is not None


# ---------------------------------------------------------------------------
# GET /api/audit/stats
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, results_by_call: list[list[dict]]):
        self._results = list(results_by_call)
        self._call_idx = 0

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        if self._call_idx < len(self._results):
            rows = self._results[self._call_idx]
            self._call_idx += 1
            return rows[0] if rows else {}
        return {}

    def fetchall(self):
        if self._call_idx < len(self._results):
            rows = self._results[self._call_idx]
            self._call_idx += 1
            return rows
        return []


class TestAuditStats:
    def test_stats_returns_aggregates(self, client, monkeypatch):
        summary = [{"total": 10, "successes": 9, "avg_latency": 1500.0, "total_cost": Decimal("0.05")}]
        by_model = [{"model_used": "deepseek/deepseek-chat", "cnt": 7, "cost": Decimal("0.02")},
                     {"model_used": "claude", "cnt": 3, "cost": Decimal("0.03")}]
        by_intent = [{"intent": "aggregation", "cnt": 5}, {"intent": "simple_query", "cnt": 5}]
        by_source = [{"source_name": "ecommerce_pg", "cnt": 10}]
        by_user: list[dict] = []

        @contextmanager
        def fake_cursor(dict_rows=True):
            yield _FakeCursor([summary, by_model, by_intent, by_source, by_user])

        monkeypatch.setattr(audit_module, "get_cursor", fake_cursor)
        resp = client.get("/api/audit/stats?days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 10
        assert body["success_rate"] == 0.9
        assert "deepseek/deepseek-chat" in body["by_model"]
        assert body["by_intent"]["aggregation"] == 5

    def test_stats_empty(self, client, monkeypatch):
        @contextmanager
        def fake_cursor(dict_rows=True):
            yield _FakeCursor([[{"total": 0, "successes": 0, "avg_latency": None, "total_cost": 0}],
                                [], [], [], []])

        monkeypatch.setattr(audit_module, "get_cursor", fake_cursor)
        resp = client.get("/api/audit/stats?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 0
        assert body["success_rate"] == 0.0
