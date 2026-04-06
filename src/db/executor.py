"""SQL execution layer with read-only safety filtering and statement timeout.

The agent only ever executes LLM-generated queries through this module. The
contract is intentionally narrow:

* Only ``SELECT`` (or ``WITH ... SELECT``) statements are accepted.
* DDL/DML keywords are blocked even if they appear in subqueries.
* The PostgreSQL session-level ``statement_timeout`` is set per-call so a
  runaway query can't hang the agent loop.
* Errors are returned as structured tuples instead of raised, so the
  self-correction node can format them into the next prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg2

from src.config import settings
from src.db.connection import get_connection

# Forbidden top-level keywords. We strip comments and string literals before
# scanning so an LLM can't sneak ``DROP`` past us inside a string constant.
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


@dataclass
class ExecutionResult:
    success: bool
    rows: list[dict[str, Any]]
    row_count: int
    error: str | None = None
    error_type: str | None = None  # "safety" / "syntax" / "runtime" / "timeout"


def _strip_for_scan(sql: str) -> str:
    s = _BLOCK_COMMENT_RE.sub(" ", sql)
    s = _LINE_COMMENT_RE.sub(" ", s)
    s = _STRING_LITERAL_RE.sub("''", s)
    return s


def _is_select_only(sql: str) -> tuple[bool, str | None]:
    """Return ``(ok, reason)``. Allows SELECT and CTE-leading WITH statements."""
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

    # Disallow multiple statements
    if ";" in stripped:
        return False, "multiple statements are not allowed"
    return True, None


def execute_sql(
    sql: str,
    *,
    timeout_seconds: int | None = None,
    max_rows: int = 1000,
) -> ExecutionResult:
    """Run a single SELECT statement and return rows + metadata.

    Args:
        sql: the SQL to execute (single statement, SELECT/WITH).
        timeout_seconds: per-statement timeout. Defaults to
            ``settings.sql_timeout_seconds``.
        max_rows: hard cap on rows returned to the agent. Anything beyond is
            silently dropped to keep prompts small.
    """
    timeout_seconds = timeout_seconds or settings.sql_timeout_seconds

    ok, reason = _is_select_only(sql)
    if not ok:
        return ExecutionResult(
            success=False,
            rows=[],
            row_count=0,
            error=f"SQL safety check failed: {reason}",
            error_type="safety",
        )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_seconds) * 1000}")
                cur.execute(sql)
                if cur.description is None:
                    return ExecutionResult(
                        success=True, rows=[], row_count=0, error=None
                    )
                columns = [desc[0] for desc in cur.description]
                fetched = cur.fetchmany(max_rows)
                rows = [dict(zip(columns, _coerce_row(row))) for row in fetched]
                return ExecutionResult(
                    success=True,
                    rows=rows,
                    row_count=len(rows),
                    error=None,
                )
    except psycopg2.errors.QueryCanceled as exc:
        return ExecutionResult(
            success=False,
            rows=[],
            row_count=0,
            error=f"query timed out after {timeout_seconds}s: {exc}",
            error_type="timeout",
        )
    except psycopg2.Error as exc:
        return ExecutionResult(
            success=False,
            rows=[],
            row_count=0,
            error=str(exc).strip(),
            error_type="runtime",
        )


def _coerce_row(row: tuple) -> tuple:
    """Convert non-JSON-serializable values to strings for downstream use."""
    out: list[Any] = []
    for v in row:
        if v is None or isinstance(v, (bool, int, float, str)):
            out.append(v)
        else:
            out.append(str(v))
    return tuple(out)
