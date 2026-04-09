"""Unit tests for the connector layer.

Coverage:

* ``DataSourceConnector._validate_sql_safety`` — moved from the legacy
  ``src.db.executor`` location; same strict semantics, accessed through the
  base class for forwards-compatibility with future connectors.
* ``ColumnMeta`` / ``TableMeta`` / ``QueryResult`` dataclass shapes.
* ``ConnectorFactory`` dialect routing + error on unknown dialect.
* ``ConnectorRegistry`` lifecycle (init / get / reset / disconnect).
* ``enrich_with_overlay`` merge semantics over both the legacy list-of-tables
  YAML structure and the new name-keyed dict structure.

DuckDB / asyncpg / aiomysql integration tests are NOT in this file — they
require the corresponding driver and a live database; they live in their
own ``tests/integration_*`` modules and are skipped by default.

Run with::

    .venv/bin/python -m pytest tests/test_connectors.py -v
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from src.connectors.base import (
    ColumnMeta,
    DataSourceConnector,
    QueryResult,
    TableMeta,
    _is_select_only,
)
from src.connectors.factory import ConnectorFactory
from src.connectors.overlay import enrich_with_overlay
from src.connectors.registry import ConnectorRegistry


# ---------------------------------------------------------------------------
# Concrete connector for testing the abstract base
# ---------------------------------------------------------------------------


class _NoopConnector(DataSourceConnector):
    """Smallest possible concrete connector. Used to test the ABC contract."""

    def __init__(self, config: dict | None = None):
        super().__init__(config or {"name": "noop", "dialect": "noop"})
        self._tables: list[TableMeta] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def test_connection(self) -> bool:
        return True

    async def execute_query(self, sql: str, timeout_seconds: int = 30, max_rows: int = 1000):
        ok, reason = self._validate_sql_safety(sql)
        if not ok:
            return self._safety_failure_result(sql, reason or "unknown")
        return QueryResult(
            success=True,
            columns=["x"],
            rows=[{"x": 1}],
            row_count=1,
            execution_time_ms=0,
            sql_executed=sql,
        )

    async def get_tables(self):
        return list(self._tables)


# ---------------------------------------------------------------------------
# SQL safety filter (now lives on DataSourceConnector)
# ---------------------------------------------------------------------------


class TestSqlSafetyFilter:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "select * from t",
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
        ],
    )
    def test_dangerous_rejected(self, sql, fragment):
        ok, reason = _is_select_only(sql)
        assert not ok
        assert fragment.lower() in (reason or "").lower()

    def test_string_literal_with_dangerous_word_is_allowed(self):
        ok, _ = _is_select_only("SELECT 'DROP TABLE users' AS msg")
        assert ok

    def test_block_comment_with_dangerous_word_is_allowed(self):
        ok, _ = _is_select_only("SELECT 1 /* DROP TABLE users */")
        assert ok

    def test_two_statements_rejected(self):
        ok, reason = _is_select_only("SELECT 1; SELECT 2")
        assert not ok
        assert "multiple" in (reason or "").lower()

    def test_concrete_connector_routes_safety_failures_uniformly(self):
        async def _run():
            c = _NoopConnector()
            return await c.execute_query("DROP TABLE users")

        result = asyncio.run(_run())
        assert result.success is False
        assert result.error_type == "safety"
        assert result.sql_executed == "DROP TABLE users"


# ---------------------------------------------------------------------------
# Dataclass smoke
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_column_meta_defaults(self):
        c = ColumnMeta(name="user_id", data_type="integer")
        assert c.nullable is True
        assert c.is_primary_key is False
        assert c.comment is None

    def test_table_meta_columns_default_empty(self):
        t = TableMeta(table_name="t", schema_name="public")
        assert t.columns == []
        assert t.layer is None

    def test_query_result_defaults(self):
        r = QueryResult(success=True)
        assert r.rows == []
        assert r.columns == []
        assert r.row_count == 0
        assert r.error is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_unknown_dialect_raises(self):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            ConnectorFactory.create({"name": "x", "dialect": "snowflake"})

    def test_postgres_lazy_import_succeeds(self):
        # Even if asyncpg isn't actually used, the class import must work.
        connector = ConnectorFactory.create(
            {
                "name": "test_pg",
                "dialect": "postgresql",
                "connection": {"host": "localhost", "port": 5432, "database": "x"},
            }
        )
        assert connector.name == "test_pg"
        assert connector.get_dialect() == "postgresql"

    def test_create_all_round_trips(self):
        configs = [
            {"name": "a", "dialect": "postgresql", "connection": {"host": "localhost"}},
            {"name": "b", "dialect": "postgresql", "connection": {"host": "localhost"}},
        ]
        out = ConnectorFactory.create_all(configs)
        assert set(out.keys()) == {"a", "b"}

    def test_create_all_requires_name(self):
        with pytest.raises(ValueError, match="missing 'name'"):
            ConnectorFactory.create_all([{"dialect": "postgresql"}])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def setup_method(self):
        ConnectorRegistry.reset_instance()

    def teardown_method(self):
        ConnectorRegistry.reset_instance()

    def test_singleton_returns_same_instance(self):
        a = ConnectorRegistry.get_instance()
        b = ConnectorRegistry.get_instance()
        assert a is b

    def test_get_unknown_raises(self):
        registry = ConnectorRegistry.get_instance()
        with pytest.raises(KeyError):
            registry.get("missing")

    def test_init_from_yaml_with_stub(self, tmp_path: Path, monkeypatch):
        # Build a minimal YAML that points at a stub dialect we register
        # via monkey-patching the factory's resolver.
        yaml_path = tmp_path / "datasources.yaml"
        yaml_path.write_text(
            textwrap.dedent(
                """
                default_source: stub
                datasources:
                  - name: stub
                    dialect: stub_dialect
                    description: "test"
                """
            ).strip()
        )

        from src.connectors import factory as factory_module

        original_resolve = factory_module._resolve_class

        def fake_resolve(dialect: str):
            if dialect == "stub_dialect":
                return _NoopConnector
            return original_resolve(dialect)

        monkeypatch.setattr(factory_module, "_resolve_class", fake_resolve)

        async def _run():
            registry = ConnectorRegistry.get_instance()
            await registry.init_from_yaml(yaml_path)
            try:
                assert registry.is_initialized
                assert registry.default_name() == "stub"
                assert registry.get("stub").name == "stub"
                assert registry.get().name == "stub"
            finally:
                await registry.disconnect_all()

        asyncio.run(_run())

    def test_env_var_expansion(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MY_HOST", "from_env")
        yaml_path = tmp_path / "datasources.yaml"
        yaml_path.write_text(
            textwrap.dedent(
                """
                default_source: x
                datasources:
                  - name: x
                    dialect: stub_dialect
                    connection:
                      host: ${MY_HOST:-fallback}
                      port: ${UNSET_VAR:-9999}
                """
            ).strip()
        )

        from src.connectors import factory as factory_module

        original_resolve = factory_module._resolve_class

        def fake_resolve(dialect: str):
            if dialect == "stub_dialect":
                return _NoopConnector
            return original_resolve(dialect)

        monkeypatch.setattr(factory_module, "_resolve_class", fake_resolve)

        async def _run():
            registry = ConnectorRegistry.get_instance()
            await registry.init_from_yaml(yaml_path)
            try:
                configs = registry.raw_configs()
                assert configs[0]["connection"]["host"] == "from_env"
                assert configs[0]["connection"]["port"] == "9999"
            finally:
                await registry.disconnect_all()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# enrich_with_overlay
# ---------------------------------------------------------------------------


class TestOverlay:
    def _meta(self) -> list[TableMeta]:
        return [
            TableMeta(
                table_name="ods_users",
                schema_name="public",
                columns=[
                    ColumnMeta(name="user_id", data_type="integer", is_primary_key=True),
                    ColumnMeta(name="email", data_type="string"),
                ],
                layer="ODS",
            ),
            TableMeta(
                table_name="dwd_orders",
                schema_name="public",
                columns=[ColumnMeta(name="order_id", data_type="integer")],
                layer="DWD",
            ),
        ]

    def test_no_overlay_path_passes_through(self):
        out = enrich_with_overlay(self._meta(), None)
        assert len(out) == 2
        first = next(t for t in out if t.name == "ods_users")
        assert first.chinese_name == ""
        assert first.layer == "ODS"
        # types come from the connector (already mapped to unified strings)
        col_types = {c.name: c.type for c in first.columns}
        assert col_types["user_id"] == "integer"

    def test_legacy_list_overlay(self, tmp_path: Path):
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            textwrap.dedent(
                """
                tables:
                  - name: ods_users
                    chinese_name: 用户表
                    description: 用户主数据
                    common_queries:
                      - "查询用户数量"
                    columns:
                      - name: user_id
                        chinese_name: 用户ID
                      - name: email
                        chinese_name: 邮箱
                """
            ).strip()
        )

        out = enrich_with_overlay(self._meta(), overlay)
        users = next(t for t in out if t.name == "ods_users")
        assert users.chinese_name == "用户表"
        assert users.description == "用户主数据"
        assert "查询用户数量" in users.common_queries
        col_lookup = {c.name: c for c in users.columns}
        assert col_lookup["user_id"].chinese_name == "用户ID"
        assert col_lookup["email"].chinese_name == "邮箱"

    def test_dict_keyed_overlay(self, tmp_path: Path):
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            textwrap.dedent(
                """
                tables:
                  ods_users:
                    chinese_name: 用户
                    columns:
                      user_id:
                        chinese_name: 用户ID
                """
            ).strip()
        )
        out = enrich_with_overlay(self._meta(), overlay)
        users = next(t for t in out if t.name == "ods_users")
        assert users.chinese_name == "用户"
        col_lookup = {c.name: c for c in users.columns}
        assert col_lookup["user_id"].chinese_name == "用户ID"

    def test_overlay_does_not_override_engine_type(self, tmp_path: Path):
        # The overlay declares a wrong type; the connector's mapped type wins.
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            textwrap.dedent(
                """
                tables:
                  ods_users:
                    columns:
                      user_id:
                        type: VARCHAR
                """
            ).strip()
        )
        out = enrich_with_overlay(self._meta(), overlay)
        users = next(t for t in out if t.name == "ods_users")
        col = next(c for c in users.columns if c.name == "user_id")
        # The introspected type was "integer"; that's what we should keep.
        assert col.type == "integer"

    def test_missing_overlay_file_is_handled(self, tmp_path: Path):
        out = enrich_with_overlay(self._meta(), tmp_path / "does-not-exist.yaml")
        assert len(out) == 2
