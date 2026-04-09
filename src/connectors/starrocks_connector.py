"""StarRocks connector — high-performance OLAP via the MySQL wire protocol.

StarRocks speaks MySQL-compatible SQL, so we use ``aiomysql`` as the driver.
The dialect differences (string concat, date functions, ILIKE, LIMIT/OFFSET
syntax) are handled by ``DIALECT_INSTRUCTIONS["starrocks"]`` in the SQL
generation prompt — this connector itself just shuttles bytes.

Connection management:
    A pool is created in :meth:`connect` and shared across the agent. The
    registry owns the lifecycle.

Timeout:
    StarRocks supports session-level ``query_timeout`` (in seconds). We set
    it once per acquired connection before running the query. On timeout the
    server cancels and aiomysql raises an OperationalError that we map to
    ``error_type="timeout"``.
"""

from __future__ import annotations

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
# StarRocks → unified type map
# ---------------------------------------------------------------------------


_STARROCKS_TYPE_MAP: dict[str, str] = {
    # integers
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "INT": "integer",
    "INTEGER": "integer",
    "BIGINT": "integer",
    "LARGEINT": "integer",
    # decimals
    "FLOAT": "decimal",
    "DOUBLE": "decimal",
    "DECIMAL": "decimal",
    "DECIMALV2": "decimal",
    "DECIMAL32": "decimal",
    "DECIMAL64": "decimal",
    "DECIMAL128": "decimal",
    # strings
    "CHAR": "string",
    "VARCHAR": "string",
    "STRING": "string",
    "BINARY": "string",
    "VARBINARY": "string",
    # dates/times
    "DATE": "date",
    "DATETIME": "timestamp",
    "TIMESTAMP": "timestamp",
    # booleans
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    # complex
    "JSON": "json",
    "ARRAY": "array",
    "MAP": "json",
    "STRUCT": "json",
    "BITMAP": "string",
    "HLL": "string",
}


def _map_starrocks_type(raw: str) -> str:
    if not raw:
        return "string"
    head = raw.upper().split("(", 1)[0].strip()
    if head.startswith("ARRAY"):
        return "array"
    return _STARROCKS_TYPE_MAP.get(head, "string")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


# StarRocks-specific MySQL error codes for cancelled queries
_TIMEOUT_ERRNOS = {1317, 3024, 1969}


class StarRocksConnector(DataSourceConnector):
    """aiomysql-backed StarRocks connector."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.dialect = "starrocks"
        connection = config.get("connection", {}) or {}
        options = config.get("options", {}) or {}

        self._host = connection.get("host", "localhost")
        self._port = int(connection.get("port", 9030))
        self._database = connection.get("database", "")
        self._user = connection.get("user", "root")
        self._password = connection.get("password", "")
        self._max_connections = int(options.get("max_connections", 5))
        self._default_timeout = int(options.get("timeout_seconds", 30))
        self._pool: Any = None  # aiomysql.Pool

    # ----- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        import aiomysql

        self._pool = await aiomysql.create_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._database or None,
            minsize=1,
            maxsize=self._max_connections,
            autocommit=True,
            connect_timeout=10,
        )
        self._connected = True
        logger.info(
            "StarRocksConnector[%s] pool ready @ %s:%d (max=%d)",
            self.name,
            self._host,
            self._port,
            self._max_connections,
        )

    async def disconnect(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
        self._connected = False

    async def test_connection(self) -> bool:
        if not self._connected:
            try:
                await self.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("StarRocksConnector[%s] connect failed: %s", self.name, exc)
                return False
        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchall()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("StarRocksConnector[%s] ping failed: %s", self.name, exc)
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

        try:
            import aiomysql

            async with self._pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(f"SET query_timeout = {int(timeout_seconds)}")
                    await cur.execute(sql)
                    fetched = await cur.fetchmany(max_rows)
                    columns = [d[0] for d in (cur.description or [])]
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            rows = [
                {col: self._coerce_row(tuple(row.values()))[i] for i, col in enumerate(columns)}
                for row in fetched
            ]
            return QueryResult(
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
                sql_executed=sql,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            errno = getattr(exc, "args", [None])[0] if getattr(exc, "args", None) else None
            msg = str(exc).strip()

            if errno in _TIMEOUT_ERRNOS or "timeout" in msg.lower() or "interrupted" in msg.lower():
                return QueryResult(
                    success=False,
                    sql_executed=sql,
                    execution_time_ms=elapsed_ms,
                    error=f"query timed out after {timeout_seconds}s: {msg}",
                    error_type="timeout",
                )
            error_type = "syntax" if "syntax" in msg.lower() or errno == 1064 else "runtime"
            return QueryResult(
                success=False,
                sql_executed=sql,
                execution_time_ms=elapsed_ms,
                error=msg,
                error_type=error_type,
            )

    # ----- introspection -----------------------------------------------------

    async def get_tables(self) -> list[TableMeta]:
        if not self._connected:
            await self.connect()

        sql = """
            SELECT c.TABLE_SCHEMA  AS schema_name,
                   c.TABLE_NAME    AS table_name,
                   c.COLUMN_NAME   AS column_name,
                   c.DATA_TYPE     AS data_type,
                   c.IS_NULLABLE   AS is_nullable,
                   c.COLUMN_KEY    AS column_key,
                   c.COLUMN_COMMENT AS column_comment,
                   c.ORDINAL_POSITION AS ordinal_position,
                   t.TABLE_COMMENT AS table_comment,
                   t.TABLE_ROWS    AS table_rows
            FROM information_schema.COLUMNS c
            LEFT JOIN information_schema.TABLES t
              ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
             AND t.TABLE_NAME  = c.TABLE_NAME
            WHERE c.TABLE_SCHEMA = %s
            ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
        """

        import aiomysql

        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (self._database,))
                rows = await cur.fetchall()

        tables: dict[tuple[str, str], TableMeta] = {}
        for r in rows:
            key = (r["schema_name"], r["table_name"])
            if key not in tables:
                tables[key] = TableMeta(
                    table_name=r["table_name"],
                    schema_name=r["schema_name"],
                    comment=r.get("table_comment") or None,
                    columns=[],
                    row_count_approx=int(r["table_rows"]) if r.get("table_rows") is not None else None,
                    layer=_infer_layer(r["table_name"]),
                )
            tables[key].columns.append(
                ColumnMeta(
                    name=r["column_name"],
                    data_type=_map_starrocks_type(str(r["data_type"])),
                    nullable=(str(r["is_nullable"]).upper() == "YES"),
                    comment=r.get("column_comment") or None,
                    is_primary_key=(str(r.get("column_key", "")).upper() == "PRI"),
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
