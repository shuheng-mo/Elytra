"""DuckDB connector — embedded analytical engine.

Why DuckDB:
    Zero-config local OLAP. Reads Parquet/CSV directly. Bundled TPC-H
    generator. Perfect for "I want a real analytical workload without
    standing up a server" demos.

Concurrency model:
    DuckDB connections are NOT thread-safe. We hold one connection per
    connector instance and serialize all access through ``self._lock``. The
    actual ``execute`` call runs on a worker thread (``asyncio.to_thread``)
    so the event loop never blocks on a long query.

Timeout:
    DuckDB has no built-in statement timeout the way PG does. We wrap each
    query in ``asyncio.wait_for`` and call ``self._conn.interrupt()`` from
    a fallback ``except`` so a runaway query is actually cancelled rather
    than just abandoned by the awaiter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from src.connectors.base import (
    ColumnMeta,
    DataSourceConnector,
    QueryResult,
    TableMeta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DuckDB → unified type map
# ---------------------------------------------------------------------------


_DUCKDB_TYPE_MAP: dict[str, str] = {
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "INTEGER": "integer",
    "INT": "integer",
    "BIGINT": "integer",
    "HUGEINT": "integer",
    "UTINYINT": "integer",
    "USMALLINT": "integer",
    "UINTEGER": "integer",
    "UBIGINT": "integer",
    "FLOAT": "decimal",
    "REAL": "decimal",
    "DOUBLE": "decimal",
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "VARCHAR": "string",
    "CHAR": "string",
    "TEXT": "string",
    "STRING": "string",
    "UUID": "string",
    "BLOB": "string",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMPTZ": "timestamp",
    "TIME": "string",
    "INTERVAL": "string",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "JSON": "json",
    "STRUCT": "json",
    "MAP": "json",
}


def _map_duckdb_type(raw: str) -> str:
    if not raw:
        return "string"
    head = raw.upper().split("(", 1)[0].strip()
    if head.endswith("[]") or head.startswith("LIST"):
        return "array"
    return _DUCKDB_TYPE_MAP.get(head, "string")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class DuckDBConnector(DataSourceConnector):
    """duckdb-backed embedded connector with async-friendly serialization."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.dialect = "duckdb"
        connection = config.get("connection", {}) or {}
        options = config.get("options", {}) or {}

        path = connection.get("database_path") or connection.get("path") or ":memory:"
        self._database_path = str(path)
        self._read_only = bool(options.get("read_only", True))
        self._default_timeout = int(options.get("timeout_seconds", 30))
        self._schema = options.get("schema", "main")
        self._conn: Any = None  # duckdb.DuckDBPyConnection
        self._lock: asyncio.Lock = asyncio.Lock()

    # ----- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        import duckdb

        path = self._database_path
        if path != ":memory:":
            p = Path(path)
            if not p.exists() and self._read_only:
                raise FileNotFoundError(
                    f"DuckDB database not found at {p}. "
                    f"Run the corresponding load script under datasets/ first."
                )
            p.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await asyncio.to_thread(
            duckdb.connect, path, read_only=self._read_only
        )
        self._connected = True
        logger.info(
            "DuckDBConnector[%s] open at %s (read_only=%s)",
            self.name,
            path,
            self._read_only,
        )

    async def disconnect(self) -> None:
        if self._conn is not None:
            try:
                await asyncio.to_thread(self._conn.close)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DuckDBConnector[%s] close failed: %s", self.name, exc)
            self._conn = None
        self._connected = False

    async def test_connection(self) -> bool:
        if not self._connected:
            try:
                await self.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("DuckDBConnector[%s] connect failed: %s", self.name, exc)
                return False
        try:
            async with self._lock:
                await asyncio.to_thread(
                    lambda: self._conn.execute("SELECT 1").fetchone()
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("DuckDBConnector[%s] ping failed: %s", self.name, exc)
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
        t0 = time.perf_counter()

        def _run() -> tuple[list[str], list[tuple]]:
            cur = self._conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(max_rows)
            return cols, rows

        try:
            async with self._lock:
                columns, raw_rows = await asyncio.wait_for(
                    asyncio.to_thread(_run),
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError:
            try:
                self._conn.interrupt()
            except Exception:  # noqa: BLE001
                pass
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=f"query timed out after {timeout_seconds}s",
                error_type="timeout",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            msg = str(exc).strip()
            error_type = "syntax" if "syntax" in msg.lower() or "parser" in msg.lower() else "runtime"
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=msg,
                error_type=error_type,
            )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        rows = [dict(zip(columns, self._coerce_row(r))) for r in raw_rows]
        return QueryResult(
            success=True,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=elapsed_ms,
            sql_executed=sql,
        )

    # ----- introspection -----------------------------------------------------

    async def get_tables(self) -> list[TableMeta]:
        if not self._connected:
            await self.connect()

        # DuckDB ships information_schema; same shape as Postgres minus comments.
        cols_sql = """
            SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position
        """

        def _run() -> list[tuple]:
            return self._conn.execute(cols_sql).fetchall()

        async with self._lock:
            rows = await asyncio.to_thread(_run)

        tables: dict[tuple[str, str], TableMeta] = {}
        for r in rows:
            schema, table, col, dtype, nullable, _pos = r
            key = (schema, table)
            if key not in tables:
                tables[key] = TableMeta(
                    table_name=table,
                    schema_name=schema,
                    comment=None,
                    columns=[],
                    row_count_approx=None,
                    layer=None,
                )
            tables[key].columns.append(
                ColumnMeta(
                    name=col,
                    data_type=_map_duckdb_type(str(dtype)),
                    nullable=(str(nullable).upper() == "YES"),
                    comment=None,
                    is_primary_key=False,  # DuckDB info_schema doesn't expose this cheaply
                )
            )
        return list(tables.values())
