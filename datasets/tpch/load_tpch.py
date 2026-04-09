"""Generate a local TPC-H DuckDB database via DuckDB's bundled tpch extension.

Usage::

    python datasets/tpch/load_tpch.py             # SF=0.1 (default)
    python datasets/tpch/load_tpch.py --sf 1.0    # ~6M lineitem rows

SF=0.1 is the recommended default for the Elytra demo: it generates ~750k
total rows across the 8 TPC-H tables, finishes in a few seconds, and produces
a ~30MB ``.duckdb`` file. Zero external download required — DuckDB ships
everything needed.

The output path is ``datasets/tpch/tpch.duckdb``, which matches the
``database_path`` declared for ``tpch_duckdb`` in ``config/datasources.yaml``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parent / "tpch.duckdb"


def create_tpch_database(db_path: Path = DEFAULT_DB, scale_factor: float = 0.1) -> None:
    """Build a TPC-H DuckDB database at ``db_path``.

    Idempotent in the sense that re-running drops the existing file first;
    DuckDB doesn't tolerate overwriting an in-use database file in place.
    """
    import duckdb

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        print(f"removing existing database at {db_path}")
        db_path.unlink()

    print(f"opening DuckDB at {db_path}")
    conn = duckdb.connect(str(db_path))
    try:
        print("installing + loading tpch extension…")
        conn.execute("INSTALL tpch;")
        conn.execute("LOAD tpch;")
        print(f"running dbgen(sf={scale_factor})…")
        conn.execute(f"CALL dbgen(sf={scale_factor});")

        print("\nGenerated tables:")
        rows = conn.execute("SHOW TABLES").fetchall()
        for (table_name,) in rows:
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"  {table_name:20s} {count:>10,d} rows")
    finally:
        conn.close()

    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"\nDone. {db_path} ({size_mb:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sf",
        type=float,
        default=0.1,
        help="scale factor (default: 0.1 — ~750k total rows)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_DB,
        help=f"output .duckdb path (default: {DEFAULT_DB})",
    )
    args = parser.parse_args()

    try:
        create_tpch_database(args.out, args.sf)
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
