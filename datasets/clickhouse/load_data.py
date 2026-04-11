"""Load Elytra e-commerce seed data from PostgreSQL → ClickHouse.

Why PG (not DuckDB as the spec suggested):
    Elytra's canonical demo data lives in `db/init.sql` + `db/seed_data.sql`
    and is already loaded into the `elytra-db` PG container. Round-tripping
    through DuckDB would add an intermediate step for no benefit.

Type coercions applied:
    * PG DECIMAL → Python Decimal → float (ClickHouse Float64)
    * PG NULL → '' for LowCardinality(String) NOT NULL columns
      (ods_products.category_l2/brand, ods_users.gender, etc.)
    * PG `TIMESTAMP WITH TIME ZONE` → naive datetime (strip tz)

Idempotent: each table is TRUNCATE'd before insert, so re-running is safe.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger("elytra.load_clickhouse")


# ---------------------------------------------------------------------------
# Connection params (override via env)
# ---------------------------------------------------------------------------

PG_HOST = os.environ.get("DB_HOST", "localhost")
PG_PORT = int(os.environ.get("DB_PORT", "5432"))
PG_DB = os.environ.get("DB_NAME", "Elytra")
PG_USER = os.environ.get("DB_USER", "Elytra")
PG_PASSWORD = os.environ.get("DB_PASSWORD", "Elytra_dev")

CH_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CH_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CH_DB = os.environ.get("CLICKHOUSE_DB", "elytra")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")


# ---------------------------------------------------------------------------
# Per-table column plan: (pg_select, ch_table, ch_columns, coercers)
# Column order in `ch_columns` MUST match `pg_select`.
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _to_float_nn(v: Any) -> float:
    """Non-null float — 0.0 if source is NULL."""
    return 0.0 if v is None else _to_float(v)


def _lc_str(v: Any) -> str:
    """LowCardinality(String) is NOT NULL in our CH schema → coerce None to ''."""
    return "" if v is None else str(v)


def _str_nullable(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _to_datetime_naive(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return v


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    return int(v)


def _to_int_nn(v: Any) -> int:
    return 0 if v is None else int(v)


TABLES = [
    {
        "name": "ods_orders",
        "pg_sql": """
            SELECT order_id, user_id, product_id, quantity, unit_price,
                   total_amount, order_status, payment_method, created_at, updated_at
            FROM ods_orders
        """,
        "ch_columns": [
            "order_id", "user_id", "product_id", "quantity", "unit_price",
            "total_amount", "order_status", "payment_method", "created_at", "updated_at",
        ],
        "coercers": [
            _to_int_nn, _to_int_nn, _to_int_nn, _to_int_nn, _to_float_nn,
            _to_float_nn, _lc_str, _lc_str, _to_datetime_naive, _to_datetime_naive,
        ],
    },
    {
        "name": "ods_users",
        "pg_sql": """
            SELECT user_id, username, email, phone, gender, birth_date, city,
                   province, register_date, user_level
            FROM ods_users
        """,
        "ch_columns": [
            "user_id", "username", "email", "phone", "gender", "birth_date",
            "city", "province", "register_date", "user_level",
        ],
        "coercers": [
            _to_int_nn, _lc_str, _str_nullable, _str_nullable, _lc_str,
            lambda v: v,  # Date passes through
            _lc_str, _lc_str, _to_datetime_naive, _lc_str,
        ],
    },
    {
        "name": "ods_products",
        "pg_sql": """
            SELECT product_id, product_name, category_l1, category_l2, brand,
                   price, cost, stock, status, created_at
            FROM ods_products
        """,
        "ch_columns": [
            "product_id", "product_name", "category_l1", "category_l2", "brand",
            "price", "cost", "stock", "status", "created_at",
        ],
        "coercers": [
            _to_int_nn, _lc_str, _lc_str, _lc_str, _lc_str,
            _to_float_nn, _to_float, _to_int, _lc_str, _to_datetime_naive,
        ],
    },
    {
        "name": "dwd_order_detail",
        "pg_sql": """
            SELECT order_id, user_id, username, user_level, user_city, user_province,
                   product_id, product_name, category_l1, category_l2, brand,
                   quantity, unit_price, total_amount, cost_amount, profit,
                   order_status, payment_method, order_date, order_hour, created_at
            FROM dwd_order_detail
        """,
        "ch_columns": [
            "order_id", "user_id", "username", "user_level", "user_city", "user_province",
            "product_id", "product_name", "category_l1", "category_l2", "brand",
            "quantity", "unit_price", "total_amount", "cost_amount", "profit",
            "order_status", "payment_method", "order_date", "order_hour", "created_at",
        ],
        "coercers": [
            _to_int_nn, _to_int_nn, _lc_str, _lc_str, _lc_str, _lc_str,
            _to_int_nn, _lc_str, _lc_str, _lc_str, _lc_str,
            _to_int_nn, _to_float_nn, _to_float_nn, _to_float, _to_float,
            _lc_str, _lc_str, lambda v: v, _to_int_nn, _to_datetime_naive,
        ],
    },
    {
        "name": "dws_daily_sales",
        "pg_sql": """
            SELECT stat_date, category_l1, order_count, total_amount, total_profit,
                   unique_buyers, avg_order_amount
            FROM dws_daily_sales
        """,
        "ch_columns": [
            "stat_date", "category_l1", "order_count", "total_amount", "total_profit",
            "unique_buyers", "avg_order_amount",
        ],
        "coercers": [
            lambda v: v, _lc_str, _to_int_nn, _to_float_nn, _to_float_nn,
            _to_int_nn, _to_float_nn,
        ],
    },
    {
        "name": "dws_user_activity",
        "pg_sql": """
            SELECT stat_date, user_level, active_users, new_users, total_orders, total_amount
            FROM dws_user_activity
        """,
        "ch_columns": [
            "stat_date", "user_level", "active_users", "new_users", "total_orders", "total_amount",
        ],
        "coercers": [
            lambda v: v, _lc_str, _to_int_nn, _to_int_nn, _to_int_nn, _to_float_nn,
        ],
    },
]


# ---------------------------------------------------------------------------
# Main ETL
# ---------------------------------------------------------------------------


def _read_pg(cur, sql: str, coercers: list) -> list[tuple]:
    cur.execute(sql)
    rows = cur.fetchall()
    return [tuple(c(v) for c, v in zip(coercers, row)) for row in rows]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed. Run `uv sync` first.")
        return 1

    try:
        import clickhouse_connect
    except ImportError:
        logger.error("clickhouse-connect not installed. Run `uv sync` first.")
        return 1

    # ----- Connect -----
    logger.info("connecting to PG @ %s:%d/%s", PG_HOST, PG_PORT, PG_DB)
    pg_conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
    )

    logger.info("connecting to ClickHouse @ %s:%d/%s", CH_HOST, CH_PORT, CH_DB)
    ch = clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        database=CH_DB,
        username=CH_USER,
        password=CH_PASSWORD,
    )

    # Sanity: CH schema should already exist (container init) — verify.
    try:
        existing = ch.query(
            "SELECT name FROM system.tables WHERE database = %(db)s",
            parameters={"db": CH_DB},
        ).result_rows
    except Exception as exc:
        logger.error("failed to query system.tables: %s", exc)
        logger.error("did you start the container? see docker/clickhouse/docker-compose.clickhouse.yml")
        return 2

    existing_names = {r[0] for r in existing}
    expected = {t["name"] for t in TABLES}
    missing = expected - existing_names
    if missing:
        logger.warning("tables missing in ClickHouse (%s). Running create_tables.sql…", missing)
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "create_tables.sql"), "r", encoding="utf-8") as f:
            ddl_text = f.read()
        for stmt in ddl_text.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    ch.command(stmt)
                except Exception as exc:
                    logger.error("DDL failed on: %s\n%s", stmt[:80], exc)
                    return 3

    # ----- Load each table -----
    total = 0
    with pg_conn.cursor() as cur:
        for tbl in TABLES:
            name = tbl["name"]
            logger.info("loading %s …", name)

            # Truncate existing CH data (idempotent re-run)
            ch.command(f"TRUNCATE TABLE IF EXISTS {CH_DB}.{name}")

            rows = _read_pg(cur, tbl["pg_sql"], tbl["coercers"])
            if not rows:
                logger.warning("  %s: no rows in PG, skipping", name)
                continue

            ch.insert(
                table=name,
                data=rows,
                column_names=tbl["ch_columns"],
                database=CH_DB,
            )
            logger.info("  %s: inserted %d rows", name, len(rows))
            total += len(rows)

    pg_conn.close()

    # Summary
    logger.info("done. total rows written: %d", total)
    for tbl in TABLES:
        cnt = ch.query(f"SELECT count() FROM {CH_DB}.{tbl['name']}").result_rows[0][0]
        logger.info("  %s: %d rows in CH", tbl["name"], cnt)

    return 0


if __name__ == "__main__":
    sys.exit(main())
