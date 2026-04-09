"""Unit tests for the FastAPI surface.

These tests use FastAPI's ``TestClient`` (httpx-backed) so we don't need a
running uvicorn process. The agent and the database are stubbed:

* ``ConnectorRegistry`` is bootstrapped with an in-memory stub connector
  before each test that needs it (the real lifespan would try to connect
  to PG / DuckDB / StarRocks).
* ``run_agent_async`` is replaced with a fake coroutine that returns a
  canned final state.
* ``get_cursor`` (used by /api/history) is replaced with a fake context
  manager backed by an in-memory list of rows.

Run with::

    .venv/bin/python -m pytest tests/test_api.py -v
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api import history as history_module
from src.api import query as query_module
from src.api import schema as schema_module
from src.connectors.base import QueryResult, TableMeta
from src.connectors.registry import ConnectorRegistry
from src.main import app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


class _StubConnector:
    """Tiny in-memory stand-in for a real DataSourceConnector."""

    def __init__(self, name: str = "stub_pg", dialect: str = "postgresql"):
        self.name = name
        self.dialect = dialect
        self.description = f"stub source ({dialect})"
        self._connected = True

    def get_dialect(self) -> str:
        return self.dialect

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def test_connection(self) -> bool:
        return True

    async def execute_query(self, sql, timeout_seconds=30, max_rows=1000):
        return QueryResult(success=True, columns=["x"], rows=[{"x": 1}], row_count=1, execution_time_ms=1, sql_executed=sql)

    async def get_tables(self):
        return [TableMeta(table_name="stub_table", schema_name="public", columns=[])]


@pytest.fixture
def client() -> TestClient:
    """TestClient with a pre-populated ConnectorRegistry stub.

    The real lifespan event would try to connect to PG / DuckDB / StarRocks;
    we bypass it and inject an in-memory stub so /api/query, /api/schema, and
    /api/datasources have something to look up.
    """
    from src.retrieval.schema_loader import SchemaLoader

    ConnectorRegistry.reset_instance()
    SchemaLoader.clear_cache()
    registry = ConnectorRegistry.get_instance()
    stub = _StubConnector()
    registry._connectors["stub_pg"] = stub  # noqa: SLF001
    registry._default_source = "stub_pg"  # noqa: SLF001
    registry._raw_configs = [{"name": "stub_pg", "dialect": "postgresql", "overlay": None}]  # noqa: SLF001
    registry._initialized = True  # noqa: SLF001

    yield TestClient(app)

    ConnectorRegistry.reset_instance()
    SchemaLoader.clear_cache()


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/query
# ---------------------------------------------------------------------------


def _fake_run_agent(*, success: bool = True, retries: int = 0) -> Any:
    """Build a fake async ``run_agent_async`` returning canned final state."""

    async def _runner(
        user_query: str,
        session_id: str = "",
        sql_dialect: str = "postgresql",
        active_source: str = "",
    ):
        return {
            "user_query": user_query,
            "session_id": session_id,
            "active_source": active_source,
            "intent": "aggregation",
            "retrieved_schemas": [{"table": "dwd_order_detail"}],
            "model_used": "fake-model",
            "complexity_score": 2,
            "generated_sql": "SELECT category_l1, SUM(total_amount) FROM dwd_order_detail GROUP BY 1",
            "execution_success": success,
            "execution_result": [{"category_l1": "电子产品", "sum": 99999}] if success else None,
            "execution_error": None if success else "syntax error",
            "row_count": 1 if success else 0,
            "retry_count": retries,
            "correction_history": [],
            "final_answer": "查询结果：1 行",
            "visualization_hint": "bar_chart" if success else None,
            "latency_ms": 1234,
            "token_count": 567,
        }

    return _runner


@pytest.fixture
def stub_persist(monkeypatch):
    """Replace _persist_history with a no-op so /api/query doesn't touch DB."""
    calls: list[dict] = []

    def _fake_persist(state):
        calls.append(state)

    monkeypatch.setattr(query_module, "_persist_history", _fake_persist)
    return calls


class TestPostQuery:
    def test_success_response_shape(self, client, monkeypatch, stub_persist):
        monkeypatch.setattr(query_module, "run_agent_async", _fake_run_agent(success=True))
        resp = client.post(
            "/api/query",
            json={"query": "按一级品类统计销售额", "session_id": "abc"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["source"] == "stub_pg"
        assert body["dialect"] == "postgresql"
        assert body["intent"] == "aggregation"
        assert body["generated_sql"].startswith("SELECT")
        assert body["result"] == [{"category_l1": "电子产品", "sum": 99999}]
        assert body["visualization_hint"] == "bar_chart"
        assert body["model_used"] == "fake-model"
        assert body["retry_count"] == 0
        assert body["latency_ms"] == 1234
        assert body["token_count"] == 567
        # _persist_history was called once
        assert len(stub_persist) == 1
        assert stub_persist[0]["session_id"] == "abc"

    def test_failure_propagates_error(self, client, monkeypatch, stub_persist):
        monkeypatch.setattr(
            query_module, "run_agent_async", _fake_run_agent(success=False, retries=3)
        )
        resp = client.post(
            "/api/query",
            json={"query": "x", "session_id": "abc"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "syntax error"
        assert body["retry_count"] == 3
        assert body["result"] is None

    def test_explicit_source_routes_correctly(self, client, monkeypatch, stub_persist):
        captured: dict[str, str] = {}

        async def _capture(user_query, session_id="", sql_dialect="postgresql", active_source=""):
            captured["source"] = active_source
            captured["dialect"] = sql_dialect
            return {
                "user_query": user_query,
                "execution_success": True,
                "row_count": 0,
                "final_answer": "ok",
                "retry_count": 0,
                "latency_ms": 0,
                "token_count": 0,
            }

        monkeypatch.setattr(query_module, "run_agent_async", _capture)
        resp = client.post("/api/query", json={"query": "x", "source": "stub_pg"})
        assert resp.status_code == 200
        assert captured["source"] == "stub_pg"
        assert captured["dialect"] == "postgresql"

    def test_unknown_source_returns_400(self, client, stub_persist):
        resp = client.post("/api/query", json={"query": "x", "source": "no_such_source"})
        assert resp.status_code == 400
        assert "no_such_source" in resp.json()["detail"]

    def test_empty_query_rejected_by_validation(self, client):
        resp = client.post("/api/query", json={"query": ""})
        assert resp.status_code == 422  # Pydantic min_length=1

    def test_agent_exception_returns_500(self, client, monkeypatch, stub_persist):
        async def boom(**kwargs):
            raise RuntimeError("agent crashed")

        monkeypatch.setattr(query_module, "run_agent_async", boom)
        resp = client.post(
            "/api/query",
            json={"query": "x"},
        )
        assert resp.status_code == 500
        assert "agent" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/datasources
# ---------------------------------------------------------------------------


class TestGetDatasources:
    def test_lists_registered_sources(self, client):
        resp = client.get("/api/datasources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default"] == "stub_pg"
        names = [d["name"] for d in body["datasources"]]
        assert "stub_pg" in names
        stub_entry = next(d for d in body["datasources"] if d["name"] == "stub_pg")
        assert stub_entry["dialect"] == "postgresql"
        assert stub_entry["connected"] is True
        assert stub_entry["table_count"] == 1
        assert stub_entry["is_default"] is True


# ---------------------------------------------------------------------------
# GET /api/schema
# ---------------------------------------------------------------------------


class TestGetSchema:
    def test_returns_default_source_schema(self, client):
        resp = client.get("/api/schema")
        assert resp.status_code == 200
        body = resp.json()
        # The stub connector exposes a single un-layered table → "OTHER" bucket
        assert "layers" in body
        all_tables = [t for tables in body["layers"].values() for t in tables]
        names = {t["table"] for t in all_tables}
        assert "stub_table" in names

    def test_unknown_source_returns_400(self, client):
        resp = client.get("/api/schema?source=no_such_source")
        assert resp.status_code == 400

    def test_explicit_source_param_routes(self, client):
        resp = client.get("/api/schema?source=stub_pg")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/history
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params

    def fetchall(self):
        return self._rows


@pytest.fixture
def stub_history_db(monkeypatch):
    """Replace get_cursor with a context manager backed by canned rows."""
    state: dict[str, Any] = {"rows": [], "cursor": None}

    @contextmanager
    def fake_get_cursor(dict_rows: bool = True):
        cur = _FakeCursor(state["rows"])
        state["cursor"] = cur
        try:
            yield cur
        finally:
            pass

    monkeypatch.setattr(history_module, "get_cursor", fake_get_cursor)
    return state


def _row(i: int, session: str = "abc", success: bool = True) -> dict:
    return {
        "id": i,
        "session_id": session,
        "user_query": f"query #{i}",
        "intent": "aggregation",
        "generated_sql": f"SELECT {i}",
        "execution_success": success,
        "retry_count": 0,
        "model_used": "fake",
        "latency_ms": 100 + i,
        "token_count": 200 + i,
        "estimated_cost": 0.0001,
        "created_at": dt.datetime(2026, 4, 6, 12, 0, i),
    }


class TestGetHistory:
    def test_empty_history(self, client, stub_history_db):
        stub_history_db["rows"] = []
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert resp.json() == {"history": []}

    def test_returns_rows(self, client, stub_history_db):
        stub_history_db["rows"] = [_row(1), _row(2)]
        resp = client.get("/api/history?session_id=abc&limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["history"]) == 2
        assert body["history"][0]["id"] == 1
        assert body["history"][0]["user_query"] == "query #1"
        assert body["history"][0]["model_used"] == "fake"
        # session_id was forwarded as a parameterized value, not interpolated
        cur = stub_history_db["cursor"]
        assert cur is not None
        assert "WHERE session_id = %s" in cur.last_sql
        assert cur.last_params == ("abc", 5)

    def test_no_session_id_omits_where_clause(self, client, stub_history_db):
        stub_history_db["rows"] = []
        resp = client.get("/api/history?limit=10")
        assert resp.status_code == 200
        cur = stub_history_db["cursor"]
        assert cur is not None
        assert "WHERE" not in cur.last_sql
        assert cur.last_params == (10,)

    def test_db_error_returns_500(self, client, monkeypatch):
        @contextmanager
        def boom_cursor(dict_rows: bool = True):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        monkeypatch.setattr(history_module, "get_cursor", boom_cursor)
        resp = client.get("/api/history")
        assert resp.status_code == 500
        assert "db down" in resp.json()["detail"]

    def test_limit_validation(self, client):
        # > 200 should be rejected by FastAPI's Query(le=200)
        resp = client.get("/api/history?limit=999")
        assert resp.status_code == 422
