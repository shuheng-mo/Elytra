"""ClickHouse connector — columnar OLAP engine via HTTP interface.

Why ClickHouse:
    Widely deployed in China (ByteDance, Kuaishou, Bilibili, Ctrip, Tencent
    Ads) for high-throughput analytics. Has no native natural-language query
    layer, which is exactly the gap Elytra fills.

Concurrency model:
    ``clickhouse_connect`` is a synchronous driver. We follow the DuckDB
    pattern: one client per connector instance, serialize access with
    ``asyncio.Lock``, offload blocking calls to a worker thread via
    ``asyncio.to_thread`` so the event loop never stalls.

Timeout:
    ClickHouse supports native ``max_execution_time`` as a per-query setting
    (server-side cancellation). We pass it through and additionally wrap the
    call in ``asyncio.wait_for`` as a belt-and-braces fallback.

SQL safety:
    The base-class filter already blocks UPDATE/DELETE/DROP globally, which
    is convenient because MergeTree doesn't support mutating DML anyway.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.connectors.base import (
    ColumnMeta,
    DataSourceConnector,
    QueryResult,
    TableMeta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ClickHouse → unified type map
# ---------------------------------------------------------------------------


_CLICKHOUSE_TYPE_MAP: dict[str, str] = {
    # integers
    "UInt8": "integer",
    "UInt16": "integer",
    "UInt32": "integer",
    "UInt64": "integer",
    "UInt128": "integer",
    "UInt256": "integer",
    "Int8": "integer",
    "Int16": "integer",
    "Int32": "integer",
    "Int64": "integer",
    "Int128": "integer",
    "Int256": "integer",
    # decimals / floats
    "Float32": "decimal",
    "Float64": "decimal",
    "Decimal": "decimal",
    "Decimal32": "decimal",
    "Decimal64": "decimal",
    "Decimal128": "decimal",
    "Decimal256": "decimal",
    # strings
    "String": "string",
    "FixedString": "string",
    "UUID": "string",
    "IPv4": "string",
    "IPv6": "string",
    "Enum8": "string",
    "Enum16": "string",
    # dates / times
    "Date": "date",
    "Date32": "date",
    "DateTime": "timestamp",
    "DateTime64": "timestamp",
    # booleans
    "Bool": "boolean",
    # complex
    "Array": "array",
    "Tuple": "json",
    "Map": "json",
    "JSON": "json",
    "Object": "json",
}


_TYPE_WRAPPERS = ("LowCardinality", "Nullable")


def _unwrap_type(raw: str) -> tuple[str, bool]:
    """Strip ``LowCardinality(...)`` / ``Nullable(...)`` wrappers.

    Returns ``(inner_type, nullable)`` — the wrappers can nest in any order
    (e.g. ``LowCardinality(Nullable(String))``) so we loop until neither
    wrapper is left on the outside.
    """
    s = raw.strip()
    nullable = False
    changed = True
    while changed:
        changed = False
        for wrapper in _TYPE_WRAPPERS:
            prefix = f"{wrapper}("
            if s.startswith(prefix) and s.endswith(")"):
                s = s[len(prefix):-1].strip()
                if wrapper == "Nullable":
                    nullable = True
                changed = True
    return s, nullable


def _map_clickhouse_type(raw: str) -> str:
    if not raw:
        return "string"
    inner, _ = _unwrap_type(raw)
    if inner.startswith("Array("):
        return "array"
    # Strip precision / scale: "Decimal(18, 4)" → "Decimal"
    head = inner.split("(", 1)[0].strip()
    return _CLICKHOUSE_TYPE_MAP.get(head, "string")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


# ClickHouse error codes we care about
_CH_SYNTAX_CODES = {62, 46, 47}   # SYNTAX_ERROR, UNKNOWN_FUNCTION, UNKNOWN_IDENTIFIER
_CH_TIMEOUT_CODES = {159, 160}    # TIMEOUT_EXCEEDED, TOO_SLOW


class ClickHouseConnector(DataSourceConnector):
    """clickhouse_connect-backed connector with async-friendly serialization."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.dialect = "clickhouse"
        connection = config.get("connection", {}) or {}
        options = config.get("options", {}) or {}

        self._host = connection.get("host", "localhost")
        self._port = int(connection.get("port", 8123))
        self._database = connection.get("database", "default")
        self._user = connection.get("user", "default")
        self._password = connection.get("password", "") or ""
        self._default_timeout = int(options.get("timeout_seconds", 30))
        self._client: Any = None  # clickhouse_connect.driver.Client
        self._lock: asyncio.Lock = asyncio.Lock()

    # ----- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        import clickhouse_connect

        def _open():
            return clickhouse_connect.get_client(
                host=self._host,
                port=self._port,
                database=self._database,
                username=self._user,
                password=self._password,
                connect_timeout=10,
                send_receive_timeout=self._default_timeout,
            )

        self._client = await asyncio.to_thread(_open)
        self._connected = True
        logger.info(
            "ClickHouseConnector[%s] client ready @ %s:%d/%s",
            self.name,
            self._host,
            self._port,
            self._database,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await asyncio.to_thread(self._client.close)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ClickHouseConnector[%s] close failed: %s", self.name, exc
                )
            self._client = None
        self._connected = False

    async def test_connection(self) -> bool:
        if not self._connected:
            try:
                await self.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ClickHouseConnector[%s] connect failed: %s", self.name, exc
                )
                return False
        try:
            async with self._lock:
                result = await asyncio.to_thread(self._client.query, "SELECT 1")
            return bool(result.result_rows) and result.result_rows[0][0] == 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ClickHouseConnector[%s] ping failed: %s", self.name, exc
            )
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

        # Prefer LIMIT-wrapping at the driver level so we don't pull the
        # entire result set over the wire. ClickHouse has no equivalent of
        # asyncpg's fetchmany; we rely on max_execution_time + LIMIT.
        settings = {"max_execution_time": int(timeout_seconds)}

        def _run() -> tuple[list[str], list[tuple]]:
            result = self._client.query(sql, settings=settings)
            cols = list(result.column_names)
            rows = list(result.result_rows)
            if len(rows) > max_rows:
                rows = rows[:max_rows]
            return cols, rows

        try:
            async with self._lock:
                columns, raw_rows = await asyncio.wait_for(
                    asyncio.to_thread(_run),
                    timeout=timeout_seconds + 5,  # give CH a chance to self-cancel first
                )
        except asyncio.TimeoutError:
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
            code = getattr(exc, "code", None)
            msg_lower = msg.lower()

            if code in _CH_TIMEOUT_CODES or "timeout" in msg_lower or "too_slow" in msg_lower:
                error_type = "timeout"
            elif (
                code in _CH_SYNTAX_CODES
                or "syntax" in msg_lower
                or "unknown identifier" in msg_lower
                or "unknown function" in msg_lower
            ):
                error_type = "syntax"
            else:
                error_type = "runtime"

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

        # system.columns has everything we need in one shot: name, type,
        # comment, is_in_primary_key, plus the table-level comment via join.
        sql = """
            SELECT
                c.table                AS table_name,
                c.name                 AS column_name,
                c.type                 AS column_type,
                c.comment              AS column_comment,
                c.is_in_primary_key    AS is_pk,
                t.comment              AS table_comment,
                t.total_rows           AS table_rows
            FROM system.columns c
            LEFT JOIN system.tables t
              ON t.database = c.database
             AND t.name     = c.table
            WHERE c.database = {db:String}
              AND t.engine NOT IN ('View', 'MaterializedView')
            ORDER BY c.table, c.position
        """

        def _run() -> list[tuple]:
            result = self._client.query(sql, parameters={"db": self._database})
            return list(result.result_rows)

        async with self._lock:
            rows = await asyncio.to_thread(_run)

        tables: dict[str, TableMeta] = {}
        for row in rows:
            table_name, col_name, col_type, col_comment, is_pk, tbl_comment, tbl_rows = row
            if table_name not in tables:
                tables[table_name] = TableMeta(
                    table_name=table_name,
                    schema_name=self._database,
                    comment=(tbl_comment or None),
                    columns=[],
                    row_count_approx=int(tbl_rows) if tbl_rows else None,
                    layer=_infer_layer(table_name),
                )
            _, nullable = _unwrap_type(str(col_type))
            tables[table_name].columns.append(
                ColumnMeta(
                    name=col_name,
                    data_type=_map_clickhouse_type(str(col_type)),
                    nullable=nullable,
                    comment=(col_comment or None),
                    is_primary_key=bool(is_pk),
                )
            )
        return list(tables.values())


def _infer_layer(table_name: str) -> str | None:
    name = table_name.lower()
    if name.startswith("ods_"):
        return "ODS"
    if name.startswith("dwd_"):
        return "DWD"
    if name.startswith("dws_"):
        return "DWS"
    return None
