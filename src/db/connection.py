"""Synchronous PostgreSQL connection helpers (Phase 1: psycopg2).

Phase 2 will swap this for an asyncpg pool. We deliberately keep the surface
small: a context-managed connection and a context-managed cursor that returns
dict rows by default.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

from src.config import settings


@contextmanager
def get_connection() -> Iterator[PgConnection]:
    """Open a PostgreSQL connection, commit on success, rollback on failure."""
    conn = psycopg2.connect(settings.database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(dict_rows: bool = True) -> Iterator:
    """Yield a cursor inside a managed connection.

    Args:
        dict_rows: when True (default), rows are returned as dicts via
            ``RealDictCursor``. Set to False for tuple rows.
    """
    with get_connection() as conn:
        cursor_factory = RealDictCursor if dict_rows else None
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()
