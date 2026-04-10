"""Unit tests for the async task architecture (Phase 2+)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api import query_async as async_module
from src.connectors.base import QueryResult, TableMeta
from src.connectors.registry import ConnectorRegistry
from src.main import app
from src.models.task import TaskStatus
from src.tasks.manager import TaskManager


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
        return QueryResult(success=True, columns=["x"], rows=[{"x": 1}],
                           row_count=1, execution_time_ms=1, sql_executed=sql)

    async def get_tables(self):
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
    # Initialize task manager on app state
    app.state.task_manager = TaskManager(max_concurrent=2)
    yield TestClient(app)
    ConnectorRegistry.reset_instance()
    SchemaLoader.clear_cache()


# ---------------------------------------------------------------------------
# TaskManager unit tests
# ---------------------------------------------------------------------------


class TestTaskManager:
    def test_create_and_get(self):
        tm = TaskManager(max_concurrent=2)
        tid = tm.create_task({"query": "test"})
        assert len(tid) == 8
        task = tm.get_task(tid)
        assert task is not None
        assert task["status"] == TaskStatus.PENDING

    def test_get_nonexistent_returns_none(self):
        tm = TaskManager()
        assert tm.get_task("nonexistent") is None

    def test_subscribe_and_unsubscribe(self):
        tm = TaskManager()
        tid = tm.create_task({"query": "test"})
        q = tm.subscribe(tid)
        assert tid in tm._subscribers
        assert q in tm._subscribers[tid]
        tm.unsubscribe(tid, q)
        assert tid not in tm._subscribers

    @pytest.mark.asyncio
    async def test_execute_success(self):
        tm = TaskManager(max_concurrent=2)
        tid = tm.create_task({"query": "test", "session_id": "", "sql_dialect": "postgresql",
                               "active_source": "stub", "user_id": ""})

        async def fake_run():
            return {"execution_success": True, "row_count": 1, "final_answer": "ok"}

        await tm.execute(tid, fake_run)
        task = tm.get_task(tid)
        assert task["status"] == TaskStatus.SUCCESS
        assert task["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        tm = TaskManager(max_concurrent=2)
        tid = tm.create_task({"query": "test", "session_id": "", "sql_dialect": "postgresql",
                               "active_source": "stub", "user_id": ""})

        async def fail_run():
            raise RuntimeError("boom")

        await tm.execute(tid, fail_run)
        task = tm.get_task(tid)
        assert task["status"] == TaskStatus.FAILED
        assert "boom" in task["error"]

    @pytest.mark.asyncio
    async def test_subscriber_receives_events(self):
        tm = TaskManager(max_concurrent=2)
        tid = tm.create_task({"query": "test", "session_id": "", "sql_dialect": "postgresql",
                               "active_source": "stub", "user_id": ""})
        q = tm.subscribe(tid)

        async def fake_run():
            return {"execution_success": True}

        await tm.execute(tid, fake_run)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # Should have at least a status event and a complete event
        types = [e["type"] for e in events]
        assert "status" in types
        assert "complete" in types


# ---------------------------------------------------------------------------
# POST /api/query/async
# ---------------------------------------------------------------------------


class TestAsyncQueryEndpoint:
    def test_submit_returns_task_id(self, client, monkeypatch):
        async def fake_agent(user_query, session_id="", sql_dialect="postgresql",
                             active_source="", user_id=""):
            return {
                "user_query": user_query, "execution_success": True,
                "row_count": 0, "final_answer": "ok",
                "retry_count": 0, "latency_ms": 0, "token_count": 0,
            }

        monkeypatch.setattr(async_module, "run_agent_async", fake_agent)
        monkeypatch.setattr(async_module, "_persist_history", lambda s: None)

        resp = client.post("/api/query/async", json={"query": "test query"})
        assert resp.status_code == 200
        body = resp.json()
        assert "task_id" in body
        assert body["status"] == "pending"
        assert "ws://" in body["ws_url"]

    def test_unknown_source_returns_400(self, client):
        resp = client.post("/api/query/async", json={"query": "x", "source": "no_such"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/task/{task_id}
# ---------------------------------------------------------------------------


class TestTaskStatusEndpoint:
    def test_not_found(self, client):
        resp = client.get("/api/task/nonexistent")
        assert resp.status_code == 404

    def test_returns_status(self, client):
        tm = app.state.task_manager
        tid = tm.create_task({"query": "test"})
        resp = client.get(f"/api/task/{tid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == tid
        assert body["status"] == "pending"
