# TPC-H Dataset (DuckDB)

Standard analytical benchmark dataset, generated locally via DuckDB's
bundled `tpch` extension. Zero external download.

## Quick start

```bash
python datasets/tpch/load_tpch.py             # SF=0.1 (default, ~30 MB)
python datasets/tpch/load_tpch.py --sf 1.0    # SF=1.0  (~250 MB, ~6M lineitem rows)
```

This generates `datasets/tpch/tpch.duckdb`, which the
`tpch_duckdb` entry in `config/datasources.yaml` reads.

## What gets generated

| Table     | SF=0.1 rows | Description                              |
|-----------|------------:|------------------------------------------|
| lineitem  | ~600,000    | Order line items (largest fact)          |
| orders    | ~150,000    | Orders header                            |
| partsupp  | ~80,000     | Part-supplier relationships              |
| part      | ~20,000     | Parts catalog                            |
| customer  | ~15,000     | Customers                                |
| supplier  | ~1,000      | Suppliers                                |
| nation    | 25          | Nations                                  |
| region    | 5           | Regions                                  |

## Re-index after loading

```bash
python -m src.retrieval.bootstrap --source tpch_duckdb
```

Then point `/api/query` at it:

```bash
curl -X POST localhost:8000/api/query -d '{
  "query": "Top 10 customers by total spend in 1995",
  "source": "tpch_duckdb"
}'
```
