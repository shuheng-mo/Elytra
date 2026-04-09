"""PostgreSQL connector — async via asyncpg.

This is the canonical reference implementation of ``DataSourceConnector``.
Other engines (DuckDB, StarRocks) follow the same shape; only the underlying
driver, type-mapping table, and timeout mechanism differ.

Connection management:
    A single :class:`asyncpg.Pool` is created in :meth:`connect` and shared
    across the agent's lifetime. The registry owns the lifecycle.

SQL safety:
    :meth:`execute_query` calls ``self._validate_sql_safety(sql)`` first.
    Anything that isn't a single SELECT/WITH gets a ``safety`` rejection
    without ever touching the database.

Timeout:
    Per-statement ``statement_timeout`` is set inside the same transaction as
    the user query, so a slow LLM-generated query can't hang the agent loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote_plus

from src.connectors.base import (
    ColumnMeta,
    DataSourceConnector,
    QueryResult,
    TableMeta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PG → unified type map
# ---------------------------------------------------------------------------


_PG_TYPE_MAP: dict[str, str] = {
    # integers
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "int2": "integer",
    "int4": "integer",
    "int8": "integer",
    "serial": "integer",
    "bigserial": "integer",
    # decimals
    "numeric": "decimal",
    "decimal": "decimal",
    "real": "decimal",
    "double precision": "decimal",
    "float4": "decimal",
    "float8": "decimal",
    "money": "decimal",
    # strings
    "character varying": "string",
    "varchar": "string",
    "character": "string",
    "char": "string",
    "text": "string",
    "uuid": "string",
    # dates/times
    "date": "date",
    "timestamp": "timestamp",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
    "timestamptz": "timestamp",
    "time": "string",
    # booleans
    "boolean": "boolean",
    "bool": "boolean",
    # json
    "json": "json",
    "jsonb": "json",
}


def _map_pg_type(raw: str) -> str:
    if not raw:
        return "string"
    key = raw.lower().strip()
    if key in _PG_TYPE_MAP:
        return _PG_TYPE_MAP[key]
    # ARRAY / USER-DEFINED / etc
    if "[]" in key or key.startswith("array"):
        return "array"
    return "string"


# ---------------------------------------------------------------------------
# DSN builder
# ---------------------------------------------------------------------------


def _build_dsn(connection: dict) -> str:
    """Translate a YAML ``connection`` block into an asyncpg DSN.

    Accepts either ``url`` (full DSN) or discrete fields
    ``host/port/database/user/password``.
    """
    if "url" in connection:
        return connection["url"]
    user = quote_plus(str(connection.get("user", "")))
    password = quote_plus(str(connection.get("password", "")))
    host = connection.get("host", "localhost")
    port = connection.get("port", 5432)
    database = connection.get("database", "postgres")
    auth = f"{user}:{password}@" if user else ""
    return f"postgresql://{auth}{host}:{port}/{database}"


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class PostgresConnector(DataSourceConnector):
    """asyncpg-backed PostgreSQL connector."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.dialect = "postgresql"
        connection = config.get("connection", {}) or {}
        options = config.get("options", {}) or {}
        self._dsn = _build_dsn(connection)
        self._schema = options.get("schema", "public")
        self._max_connections = int(options.get("max_connections", 5))
        self._default_timeout = int(options.get("timeout_seconds", 30))
        self._pool: Any = None  # asyncpg.Pool

    # ----- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        import asyncpg

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=self._max_connections,
        )
        self._connected = True
        logger.info("PostgresConnector[%s] pool ready (max=%d)", self.name, self._max_connections)

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        self._connected = False

    async def test_connection(self) -> bool:
        if not self._connected:
            try:
                await self.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("PostgresConnector[%s] connect failed: %s", self.name, exc)
                return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostgresConnector[%s] ping failed: %s", self.name, exc)
            return False

    # ----- query -------------------------------------------------------------

    async def execute_query(
        self,
        sql: str,
        timeout_seconds: int = 30,
        max_rows: int = 1000,
    ) -> QueryResult:
        ok, reason = self._validate_sql_safety(sql)
        if not ok:
            return self._safety_failure_result(sql, reason or "unknown")

        if not self._connected:
            await self.connect()

        timeout_seconds = timeout_seconds or self._default_timeout
        timeout_ms = int(timeout_seconds) * 1000
        t0 = time.perf_counter()

        try:
            import asyncpg

            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    records = await conn.fetch(sql)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            if not records:
                return QueryResult(
                    success=True,
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=elapsed_ms,
                    sql_executed=sql,
                )

            columns = list(records[0].keys())
            trimmed = records[:max_rows]
            rows = [
                dict(zip(columns, self._coerce_row(tuple(rec.values()))))
                for rec in trimmed
            ]
            return QueryResult(
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
                sql_executed=sql,
            )
        except asyncpg.exceptions.QueryCanceledError as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=f"query timed out after {timeout_seconds}s: {exc}",
                error_type="timeout",
            )
        except asyncpg.exceptions.PostgresSyntaxError as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=str(exc).strip(),
                error_type="syntax",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=str(exc).strip(),
                error_type="runtime",
            )

    # ----- introspection -----------------------------------------------------

    async def get_tables(self) -> list[TableMeta]:
        if not self._connected:
            await self.connect()

        # Single round-trip: tables, columns, and table comments via pg_catalog.
        sql = """
        WITH t AS (
            SELECT c.oid AS table_oid,
                   n.nspname AS schema_name,
                   c.relname AS table_name,
                   pg_catalog.obj_description(c.oid, 'pg_class') AS table_comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm')
              AND n.nspname = $1
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        )
        SELECT
            t.schema_name,
            t.table_name,
            t.table_comment,
            a.attname AS column_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS column_type,
            NOT a.attnotnull AS nullable,
            pg_catalog.col_description(t.table_oid, a.attnum) AS column_comment,
            EXISTS (
                SELECT 1 FROM pg_catalog.pg_index i
                WHERE i.indrelid = t.table_oid
                  AND i.indisprimary
                  AND a.attnum = ANY(i.indkey)
            ) AS is_pk,
            a.attnum AS attnum
        FROM t
        JOIN pg_catalog.pg_attribute a ON a.attrelid = t.table_oid
        WHERE a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY t.schema_name, t.table_name, a.attnum
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, self._schema)

        # Group rows into TableMeta
        tables: dict[tuple[str, str], TableMeta] = {}
        for r in rows:
            key = (r["schema_name"], r["table_name"])
            if key not in tables:
                tables[key] = TableMeta(
                    table_name=r["table_name"],
                    schema_name=r["schema_name"],
                    comment=r["table_comment"],
                    columns=[],
                    row_count_approx=None,
                    layer=_infer_layer(r["table_name"]),
                )
            tables[key].columns.append(
                ColumnMeta(
                    name=r["column_name"],
                    data_type=_map_pg_type(r["column_type"]),
                    nullable=bool(r["nullable"]),
                    comment=r["column_comment"],
                    is_primary_key=bool(r["is_pk"]),
                )
            )
        return list(tables.values())


def _infer_layer(table_name: str) -> str | None:
    """Best-effort layer inference from name prefix (Elytra convention)."""
    name = table_name.lower()
    if name.startswith("ods_"):
        return "ODS"
    if name.startswith("dwd_"):
        return "DWD"
    if name.startswith("dws_"):
        return "DWS"
    if name in ("query_history", "schema_embeddings"):
        return "SYSTEM"
    return None
