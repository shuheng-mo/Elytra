"""Typed error classification for self-correction and audit aggregation.

The connector layer already tags execution failures with a coarse
``error_type`` string (``safety`` / ``syntax`` / ``runtime`` / ``timeout``).
This module extends that vocabulary into a richer taxonomy that's stable
enough to key the experience pool and drive the audit ``top_errors`` view.

Design notes:
    - String-valued enum so DB columns can store it directly without mapping.
    - ``classify_error`` accepts either an exception or a raw message string
      because different call sites have different shapes of the failure.
    - Heuristic is keyword-first; we intentionally do NOT import the various
      DBAPI exception classes (psycopg2, clickhouse_connect, duckdb) because
      that would pull heavy dependencies into any module that classifies.
"""

from __future__ import annotations

from enum import Enum


class ErrorType(str, Enum):
    """Canonical error categories surfaced to the user and audit layer."""

    SYNTAX = "syntax"
    COLUMN_NOT_FOUND = "column_not_found"
    TABLE_NOT_FOUND = "table_not_found"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    SAFETY_VIOLATION = "safety_violation"
    PROMPT_INJECTION = "prompt_injection"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


# Substrings we look for, mapped to ErrorType. Order matters: more specific
# patterns (column_not_found) come before more generic ones (syntax).
_PATTERNS: list[tuple[tuple[str, ...], ErrorType]] = [
    # Column-not-found variants across PG / CH / DuckDB / StarRocks
    (
        (
            "column",
            "does not exist",
        ),
        ErrorType.COLUMN_NOT_FOUND,
    ),
    (
        (
            "unknown identifier",
        ),
        ErrorType.COLUMN_NOT_FOUND,
    ),
    (
        (
            "no such column",
        ),
        ErrorType.COLUMN_NOT_FOUND,
    ),
    (
        (
            "binder error",
            "column",
        ),
        ErrorType.COLUMN_NOT_FOUND,
    ),
    # Table-not-found variants
    (
        (
            "relation",
            "does not exist",
        ),
        ErrorType.TABLE_NOT_FOUND,
    ),
    (
        (
            "table",
            "doesn't exist",
        ),
        ErrorType.TABLE_NOT_FOUND,
    ),
    (
        (
            "table",
            "not found",
        ),
        ErrorType.TABLE_NOT_FOUND,
    ),
    (
        (
            "no such table",
        ),
        ErrorType.TABLE_NOT_FOUND,
    ),
    (
        (
            "unknown table",
        ),
        ErrorType.TABLE_NOT_FOUND,
    ),
    # Timeout — ClickHouse TOO_SLOW / Code 159, PG statement timeout, DuckDB interrupt
    (("statement timeout",), ErrorType.TIMEOUT),
    (("query timed out",), ErrorType.TIMEOUT),
    (("timed out",), ErrorType.TIMEOUT),
    (("too_slow",), ErrorType.TIMEOUT),
    (("code: 159",), ErrorType.TIMEOUT),
    (("execution_time",), ErrorType.TIMEOUT),
    # Permission / access denied
    (("permission denied",), ErrorType.PERMISSION_DENIED),
    (("access denied",), ErrorType.PERMISSION_DENIED),
    (("insufficient privilege",), ErrorType.PERMISSION_DENIED),
    # Safety violation (the connector-side _validate_sql_safety reject path)
    (("safety",), ErrorType.SAFETY_VIOLATION),
    (("only select",), ErrorType.SAFETY_VIOLATION),
    # Syntax — last because it's the most generic (many other errors contain "syntax")
    (("syntax error",), ErrorType.SYNTAX),
    (("syntax",), ErrorType.SYNTAX),
    (("parser error",), ErrorType.SYNTAX),
    (("parse error",), ErrorType.SYNTAX),
    (("code: 62",), ErrorType.SYNTAX),
]


def classify_error(
    error: Exception | str | None,
    *,
    connector_error_type: str | None = None,
) -> ErrorType:
    """Classify an execution failure into a canonical ``ErrorType``.

    Parameters
    ----------
    error:
        The raised exception or error message string. ``None`` yields
        ``ErrorType.UNKNOWN``.
    connector_error_type:
        Optional hint from ``QueryResult.error_type`` (connector-side coarse
        label: ``safety`` / ``syntax`` / ``timeout`` / ``runtime``). We use
        this as a prior, then refine via keyword matching on the message.

    Heuristic order:
        1. If connector returned ``safety`` or ``timeout``, trust it directly
           (these are well-defined at the connector layer).
        2. Scan the error message for keyword patterns.
        3. Fall back to ``connector_error_type`` if it maps cleanly.
        4. Finally return ``ErrorType.RUNTIME`` (or ``UNKNOWN`` if no signal).
    """
    if error is None and not connector_error_type:
        return ErrorType.UNKNOWN

    # Trust strong connector-side labels first
    if connector_error_type == "safety":
        return ErrorType.SAFETY_VIOLATION
    if connector_error_type == "timeout":
        return ErrorType.TIMEOUT

    message = str(error or "").lower()

    for needles, err_type in _PATTERNS:
        if all(needle in message for needle in needles):
            return err_type

    # Fall back to the connector's coarse label if it's something we recognize
    if connector_error_type == "syntax":
        return ErrorType.SYNTAX
    if connector_error_type == "runtime":
        return ErrorType.RUNTIME

    if message:
        return ErrorType.RUNTIME
    return ErrorType.UNKNOWN
