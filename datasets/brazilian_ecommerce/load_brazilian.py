"""Load the Brazilian E-Commerce (Olist) dataset into a local DuckDB file.

Source: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

Manual download (one-time)::

    1. Go to the Kaggle page above and download the .zip
    2. Unzip into ``datasets/brazilian_ecommerce/csv/``
    3. Run this script: ``python datasets/brazilian_ecommerce/load_brazilian.py``

The script reads each expected CSV via DuckDB's ``read_csv_auto`` and writes
one table per file. Missing files are skipped with a warning so partial
downloads still produce a usable database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "csv"
DB_PATH = HERE / "brazilian.duckdb"


# {logical_table_name: csv_file_name}
TABLE_FILES: dict[str, str] = {
    "olist_orders": "olist_orders_dataset.csv",
    "olist_order_items": "olist_order_items_dataset.csv",
    "olist_products": "olist_products_dataset.csv",
    "olist_customers": "olist_customers_dataset.csv",
    "olist_sellers": "olist_sellers_dataset.csv",
    "olist_payments": "olist_order_payments_dataset.csv",
    "olist_reviews": "olist_order_reviews_dataset.csv",
    "olist_geolocation": "olist_geolocation_dataset.csv",
    "product_category_translation": "product_category_name_translation.csv",
}


def load(csv_dir: Path = CSV_DIR, db_path: Path = DB_PATH) -> int:
    """Build the DuckDB database. Returns the number of tables loaded."""
    import duckdb

    if not csv_dir.exists():
        raise FileNotFoundError(
            f"CSV directory not found: {csv_dir}\n"
            f"Download from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce "
            f"and unzip into this directory."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        print(f"removing existing database at {db_path}")
        db_path.unlink()

    print(f"opening DuckDB at {db_path}")
    conn = duckdb.connect(str(db_path))
    loaded = 0
    try:
        for table, filename in TABLE_FILES.items():
            csv_path = csv_dir / filename
            if not csv_path.exists():
                print(f"  SKIP {table:35s} (missing {filename})")
                continue
            conn.execute(
                f"CREATE OR REPLACE TABLE {table} AS "
                f"SELECT * FROM read_csv_auto('{csv_path}')"
            )
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  OK   {table:35s} {count:>10,d} rows")
            loaded += 1
    finally:
        conn.close()

    if loaded == 0:
        print("\nNo tables were loaded — check that CSVs exist in the csv/ directory.")
    else:
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"\nDone. {loaded} tables — {db_path} ({size_mb:.1f} MB)")
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=CSV_DIR,
        help=f"directory holding the unzipped CSVs (default: {CSV_DIR})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DB_PATH,
        help=f"output .duckdb path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    try:
        load(args.csv_dir, args.out)
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
