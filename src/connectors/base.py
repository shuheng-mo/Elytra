"""DataSource connector abstract base class.

The connector layer decouples Elytra's agent from any single SQL engine. Every
analytics query the agent runs flows through one of these objects, regardless
of whether the underlying engine is PostgreSQL, DuckDB, or StarRocks.

The interface is intentionally narrow:

* ``connect`` / ``disconnect`` — explicit lifecycle, owned by the registry.
* ``execute_query`` — read-only SQL execution with timeout + safety filtering.
* ``get_tables`` — engine-agnostic schema introspection (returns ``TableMeta``).
* ``get_dialect`` — short string used by the SQL generator to pick a prompt.

SQL safety filtering (``_validate_sql_safety``) is implemented once on the base
class and reused by every concrete connector. The filter is a port of the
Phase 1 PostgreSQL executor and is intentionally strict: it strips comments
and string literals before scanning so the LLM cannot smuggle a forbidden
keyword past us inside a string constant or comment.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Unified, engine-agnostic metadata types
# ---------------------------------------------------------------------------


@dataclass
class ColumnMeta:
    """Engine-agnostic column metadata returned by every connector."""

    name: str
    data_type: str  # unified: string/integer/decimal/date/timestamp/boolean/json/array
    nullable: bool = True
    comment: str | None = None
    is_primary_key: bool = False


@dataclass
class TableMeta:
    """Engine-agnostic table metadata returned by every connector."""

    table_name: str
    schema_name: str  # PG schema / DuckDB schema / StarRocks database
    comment: str | None = None
    columns: list[ColumnMeta] = field(default_factory=list)
    row_count_approx: int | None = None
    layer: str | None = None  # ODS/DWD/DWS — populated when applicable


@dataclass
class QueryResult:
    """Engine-agnostic query execution result.

    Field semantics intentionally mirror Phase 1's ``ExecutionResult`` so
    ``sql_executor_node`` can fold a ``QueryResult`` back into ``AgentState``
    without changing how self-correction reads errors.
    """

    success: bool
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    sql_executed: str = ""
    error: str | None = None
    error_type: str | None = None  # "safety" / "syntax" / "runtime" / "timeout"


# ---------------------------------------------------------------------------
# Shared SQL safety filter (ported from src/db/executor.py)
# ---------------------------------------------------------------------------


_FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COMMENT",
    "VACUUM",
    "REINDEX",
    "COPY",
    "MERGE",
    "CALL",
    "DO",
)

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


def _strip_for_scan(sql: str) -> str:
    """Remove comments and string literals so they can't hide forbidden keywords."""
    s = _BLOCK_COMMENT_RE.sub(" ", sql)
    s = _LINE_COMMENT_RE.sub(" ", s)
    s = _STRING_LITERAL_RE.sub("''", s)
    return s


def _is_select_only(sql: str) -> tuple[bool, str | None]:
    """Return ``(ok, reason)``. Allows SELECT and CTE-leading WITH statements.

    Strict: rejects any DDL/DML keyword anywhere outside of comments/string
    literals, and forbids more than one statement per call.
    """
    stripped = _strip_for_scan(sql).strip().rstrip(";").strip()
    if not stripped:
        return False, "empty SQL"

    head = stripped.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH"):
        return False, f"only SELECT/WITH statements allowed (got {head})"

    upper = " " + stripped.upper() + " "
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return False, f"forbidden keyword: {kw}"

    if ";" in stripped:
        return False, "multiple statements are not allowed"
    return True, None


def coerce_row(row: tuple | list) -> tuple:
    """Convert non-JSON-serializable values to strings for downstream use."""
    out: list[Any] = []
    for v in row:
        if v is None or isinstance(v, (bool, int, float, str)):
            out.append(v)
        else:
            out.append(str(v))
    return tuple(out)


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class DataSourceConnector(ABC):
    """Abstract base for every analytics data source.

    Subclasses MUST be async-safe. Concrete connectors are constructed with a
    ``config`` dict (parsed straight from ``config/datasources.yaml``) but do
    NOT establish a connection in ``__init__`` — wait for ``connect()``.
    """

    def __init__(self, config: dict):
        self.config = config
        self.name: str = config.get("name", "unnamed")
        self.dialect: str = config.get("dialect", "postgresql")
        self.description: str = config.get("description", "")
        self._connected: bool = False

    # ----- lifecycle ---------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection / create pool. Idempotent."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection / drain pool. Idempotent."""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Cheap probe — returns True if the underlying engine answers a ping."""

    # ----- queries -----------------------------------------------------------

    @abstractmethod
    async def execute_query(
        self,
        sql: str,
        timeout_seconds: int = 30,
        max_rows: int = 1000,
    ) -> QueryResult:
        """Run a SELECT and return rows + metadata.

        Concrete implementations MUST call ``self._validate_sql_safety(sql)``
        before touching the underlying engine and return a safety-error
        ``QueryResult`` on rejection.
        """

    @abstractmethod
    async def get_tables(self) -> list[TableMeta]:
        """Return all user-visible tables in unified ``TableMeta`` format."""

    # ----- introspection -----------------------------------------------------

    def get_dialect(self) -> str:
        """Short SQL dialect identifier (postgresql / duckdb / starrocks / ...)."""
        return self.dialect

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ----- shared utilities --------------------------------------------------

    @staticmethod
    def _validate_sql_safety(sql: str) -> tuple[bool, str | None]:
        """Concrete safety filter shared by every connector.

        See module-level ``_is_select_only`` for the implementation. This
        thin static-method wrapper exists so subclasses can call
        ``self._validate_sql_safety(sql)`` without re-importing.
        """
        return _is_select_only(sql)

    @staticmethod
    def _coerce_row(row: tuple | list) -> tuple:
        return coerce_row(row)

    @staticmethod
    def _safety_failure_result(sql: str, reason: str) -> QueryResult:
        """Helper: build a uniform safety-rejection result."""
        return QueryResult(
            success=False,
            sql_executed=sql,
            error=f"SQL safety check failed: {reason}",
            error_type="safety",
        )
