<div align="center">

<img src="assets/elytra-logo-hex-wordmark.svg" width="320" alt="Elytra" />

# Elytra

**LLM-powered intelligent data analysis — natural language in, SQL + visualization out**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-latest-1c3d5a.svg)](https://github.com/langchain-ai/langgraph)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Tests](https://img.shields.io/badge/tests-109%2F109%20passing-brightgreen.svg)](#testing)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[English](README_EN.md) | [简体中文](README.md)

</div>

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Supported Data Sources](#supported-data-sources)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Evaluation](#evaluation)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Elytra is an **NL→SQL intelligent analytics system** for business analysts.
You ask in plain language; the system:

1. **Classifies intent** — simple query, aggregation, multi-join, exploration, or clarification
2. **Retrieves schema** — BM25 + dense vector hybrid retrieval over a YAML data dictionary, then an LLM reranker
3. **Generates SQL** — intent-specific few-shot prompt, routed to a cheap or strong model
4. **Executes safely** — SELECT-only filter, `statement_timeout`, row cap
5. **Self-corrects** — feeds the failed SQL + error back to the LLM, up to 3 retries
6. **Formats results** — picks number / bar / line / table visualization from the result shape

> **It is not a thin NL2SQL wrapper.** Elytra ships a full ODS→DWD→DWS data warehouse, schema-aware retrieval, an Agent loop with self-correction, multi-model routing (cost vs. quality trade-off), and a quantitative evaluation harness.

The reference scenario is an e-commerce SaaS platform: 5 raw operational tables (users, products, orders, payments, behavior logs), 3 cleaned/joined wide tables (order detail, user profile, product dim), and 3 pre-aggregated DWS tables (daily sales, user activity, weekly product ranking).

---

## Key Features

| Capability | Implementation |
|:---|:---|
| **Three-layer warehouse** | ODS (5 tables) → DWD (3 wide / profile / dim) → DWS (3 pre-aggregations) |
| **Hybrid retrieval** | BM25 (custom CJK + Latin tokenizer) + pgvector HNSW cosine search + min-max normalization + weighted fusion (0.4 / 0.6) |
| **Three embedding backends** | OpenAI direct / OpenRouter (`openai/text-embedding-3-large` works) / local `sentence-transformers` (BGE family) |
| **LLM Reranker** | Phase 1 cheap-LLM JSON scoring with graceful upstream-order fallback |
| **LangGraph Agent** | 8-node state machine with intent routing, self-correction loop (up to 3 retries), error fallback |
| **Multi-model routing** | Simple → DeepSeek; multi-table / exploration / repeated failures → Claude Sonnet |
| **SELECT-only safety filter** | Strips comments + string literals before scanning 16 forbidden keywords; rejects multi-statement payloads |
| **OpenRouter-first** | One key routes every model; bare names auto-prefixed; legacy per-vendor keys still supported |
| **Visualization inference** | Dispatch on result shape (rows × cols + column names) → metric / bar / line / table |
| **Quantitative evaluation** | 14-case test set with PASS/FAIL annotations, per-category breakdown, self-correction success rate |

---

## Architecture

End-to-end call chain: Streamlit frontend → FastAPI → LangGraph agent →
retrieval / routing / execution subsystems → PostgreSQL + pgvector.

```mermaid
flowchart TB
    subgraph Client["Client"]
        UI["Streamlit Frontend<br/>frontend/app.py"]
    end

    subgraph API["FastAPI Backend"]
        QApi["POST /api/query"]
        SApi["GET /api/schema"]
        HApi["GET /api/history"]
    end

    subgraph Agent["LangGraph Agent (StateGraph)"]
        direction TB
        Intent["classify_intent"]
        Clarify["format_clarification"]
        Retrieve["retrieve_schema"]
        Gen["generate_sql"]
        Exec["execute_sql"]
        Correct["self_correction"]
        Result["format_result"]
        Err["format_error"]

        Intent -- "intent = clarification" --> Clarify
        Intent -- "other intents" --> Retrieve
        Retrieve --> Gen
        Gen --> Exec
        Exec -- "success" --> Result
        Exec -- "failure & retry < MAX" --> Correct
        Correct --> Gen
        Exec -- "failure & retry == MAX" --> Err
    end

    subgraph Sub["Core subsystems"]
        Hybrid["Hybrid Retriever<br/>BM25 + vector"]
        Rerank["LLM Reranker"]
        Router["Model Router<br/>cheap / strong"]
        Safety["SQL Safety Filter<br/>SELECT-only + timeout"]
    end

    subgraph Data["Data layer"]
        PG[("PostgreSQL 16<br/>ODS / DWD / DWS")]
        Vec[("pgvector HNSW<br/>schema_embeddings")]
    end

    UI -->|HTTP| QApi
    UI -->|HTTP| SApi
    UI -->|HTTP| HApi
    QApi -->|run_agent| Intent
    SApi --> PG
    HApi --> PG

    Retrieve --> Hybrid
    Hybrid --> Rerank
    Hybrid --> Vec
    Gen --> Router
    Exec --> Safety
    Safety --> PG

    Clarify --> END0(["END"])
    Result --> END0
    Err --> END0
```

---

## Supported Data Sources

Elytra ships a pluggable **DataSource Connector** layer. Adding a new SQL
engine takes ~100 lines of code (one `DataSourceConnector` subclass) plus a
YAML config block — no changes to the agent, retrieval, or API layers.

| Engine | Status | Use case |
|:---|:---|:---|
| **PostgreSQL** | ✅ Built-in | Default e-commerce warehouse with three layers (ODS / DWD / DWS) |
| **DuckDB** | ✅ Built-in | Embedded OLAP — bundled TPC-H generator + Brazilian Olist real-world dataset |
| **StarRocks** | ✅ Optional | High-performance OLAP, MySQL-compatible protocol, separate docker compose |

The connector contract lives in `src/connectors/base.py::DataSourceConnector`.
Each source is described by one YAML block in `config/datasources.yaml`:

```yaml
default_source: ecommerce_pg

datasources:
  - name: ecommerce_pg
    dialect: postgresql
    description: "E-commerce simulated warehouse"
    connection:
      host: ${DB_HOST:-localhost}
      port: ${DB_PORT:-5432}
      database: Elytra
    overlay: db/data_dictionary.yaml         # optional Chinese metadata

  - name: tpch_duckdb
    dialect: duckdb
    description: "TPC-H standard test dataset"
    connection:
      database_path: ./datasets/tpch/tpch.duckdb
    overlay: config/overlays/tpch_duckdb.yaml
```

API requests pick a source via the `source` field (omit it to use
`default_source`):

```bash
curl -X POST localhost:8000/api/query -d '{
  "query": "Top product category by sales last month",
  "source": "tpch_duckdb"
}'
```

`GET /api/datasources` lists every configured source with its connection
state and table count.

### Quick start: TPC-H

```bash
python datasets/tpch/load_tpch.py                    # SF=0.1 DuckDB (no download)
python -m src.retrieval.bootstrap --source tpch_duckdb
# then ask with source=tpch_duckdb
```

### Quick start: Brazilian E-Commerce

```bash
# 1) Download from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
#    Unzip into datasets/brazilian_ecommerce/csv/
python datasets/brazilian_ecommerce/load_brazilian.py
python -m src.retrieval.bootstrap --source brazilian_ecommerce
```

### Enable StarRocks (optional)

```bash
docker compose -f docker/starrocks/docker-compose.starrocks.yml up -d
# See docker/starrocks/README.md for the BE registration step
```

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Language | Python ≥ 3.11 |
| Database | PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) / DuckDB / StarRocks (optional) |
| LLM framework | [LangChain](https://github.com/langchain-ai/langchain) + [LangGraph](https://github.com/langchain-ai/langgraph) |
| Backend | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) + [Pydantic v2](https://docs.pydantic.dev/latest/) |
| Frontend | [Streamlit](https://streamlit.io/) ≥ 1.35 |
| BM25 | [rank-bm25](https://github.com/dorianbrown/rank_bm25) |
| Embeddings | OpenAI / OpenRouter / [sentence-transformers](https://www.sbert.net/) |
| DB drivers | psycopg2-binary / asyncpg / duckdb / aiomysql |
| Containers | Docker + Docker Compose |
| Package manager | [uv](https://github.com/astral-sh/uv) (recommended) |
| Testing | pytest + httpx TestClient |

---

## Getting Started

### Prerequisites

- Python ≥ 3.11
- Docker + Docker Compose (recommended)
- One LLM API key — [OpenRouter](https://openrouter.ai/) recommended (one key for every model)

### Option 1: Docker Compose (recommended)

```bash
# 1. Clone
git clone https://github.com/shuheng-mo/Elytra.git
cd Elytra

# 2. Configure
cp .env.example .env
# Edit .env and fill in OPENROUTER_API_KEY

# 3. Bring up the stack (first run pulls pgvector/pg16 + builds backend & frontend)
docker compose up --build -d

# 4. Once db is healthy, populate schema_embeddings (one-shot)
docker compose exec backend python -m src.retrieval.bootstrap

# 5. Run the eval suite end-to-end
docker compose exec backend python eval/run_eval.py
```

Service URLs:

- **Frontend UI**: <http://localhost:8501>
- **API Swagger**: <http://localhost:8000/docs>
- **Healthcheck**: <http://localhost:8000/healthz>

### Option 2: Local development

```bash
# 1. Install deps (uv recommended)
uv sync

# 2. Run a pgvector database (compose works too)
docker run -d --name elytra-db \
  -e POSTGRES_DB=Elytra -e POSTGRES_USER=Elytra -e POSTGRES_PASSWORD=Elytra_dev \
  -p 5432:5432 \
  -v "$PWD/db/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
  -v "$PWD/db/seed_data.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro" \
  pgvector/pgvector:pg16

# 3. Configure .env (point DATABASE_URL at @localhost:5432)
cp .env.example .env

# 4. Bootstrap schema_embeddings
.venv/bin/python -m src.retrieval.bootstrap

# 5. Start the backend
.venv/bin/uvicorn src.main:app --reload --port 8000

# 6. Start the frontend in another terminal
.venv/bin/streamlit run frontend/app.py
```

### Try it

Open <http://localhost:8501>, browse the data dictionary in the sidebar, then
ask one of the example questions:

- How many registered users do we have in total?
- Which product category had the highest sales last month?
- Daily order count trend over the last 7 days?
- What brand do gold-tier users buy most?
- Which city has the highest average order value?

---

## Project Structure

```text
Elytra/
├── docker-compose.yml             # 3 services: db + backend + frontend
├── Dockerfile                     # backend image
├── frontend/
│   ├── Dockerfile                 # frontend image
│   └── app.py                     # single-file Streamlit application
├── pyproject.toml                 # uv / pip deps + ruff config
├── .env.example                   # API key + model + retrieval template
│
├── config/
│   ├── datasources.yaml           # multi-source registry (PG / DuckDB / StarRocks)
│   └── overlays/                  # per-source schema enrichment YAML
│
├── db/
│   ├── init.sql                   # PG schema (11 business + 2 system tables)
│   ├── seed_data.sql              # simulated data
│   └── data_dictionary.yaml       # bilingual data dictionary (also used as ecommerce_pg overlay)
│
├── datasets/
│   ├── tpch/load_tpch.py          # DuckDB TPC-H generator (built-in dbgen)
│   └── brazilian_ecommerce/       # Olist Kaggle CSV → DuckDB loader
│
├── docker/
│   └── starrocks/                 # optional StarRocks docker compose + README
│
├── src/
│   ├── config.py                  # global config (env vars)
│   ├── main.py                    # FastAPI entrypoint with connector lifespan
│   │
│   ├── models/
│   │   ├── request.py             # QueryRequest (with `source` field)
│   │   ├── response.py            # QueryResponse / SchemaResponse / DataSourcesResponse / ...
│   │   └── state.py               # AgentState (with `active_source`)
│   │
│   ├── connectors/                # NEW: pluggable data source layer
│   │   ├── base.py                # DataSourceConnector ABC + dataclasses + safety filter
│   │   ├── postgres_connector.py  # asyncpg
│   │   ├── duckdb_connector.py    # embedded DuckDB
│   │   ├── starrocks_connector.py # aiomysql (StarRocks MySQL protocol)
│   │   ├── factory.py             # dialect → class with lazy import
│   │   ├── registry.py            # singleton, init_from_yaml, env-var expansion
│   │   └── overlay.py             # TableMeta + YAML overlay → TableInfo
│   │
│   ├── db/
│   │   ├── connection.py          # psycopg2 context managers (infra DB only)
│   │   └── executor.py            # legacy shim (re-exports + sync wrapper)
│   │
│   ├── retrieval/
│   │   ├── schema_loader.py       # YAML loader + load_from_connector + per-source cache
│   │   ├── bm25_index.py          # CJK + Latin tokenizer + BM25Okapi
│   │   ├── embedder.py            # OpenAI / OpenRouter / local backends + source-discriminated index
│   │   ├── hybrid_retriever.py    # source-bound BM25 + vector fusion
│   │   ├── reranker.py            # LLM-as-Reranker
│   │   └── bootstrap.py           # multi-source bootstrap (--source flag)
│   │
│   ├── agent/
│   │   ├── graph.py               # LangGraph state machine + run_agent_async
│   │   ├── llm.py                 # OpenRouter-first chat helper
│   │   ├── nodes/                 # 6 nodes (sql_executor is async)
│   │   └── prompts/               # intent / sql_generation (with DIALECT_INSTRUCTIONS) / ...
│   │
│   ├── router/
│   │   └── model_router.py        # rule engine: cheap / strong routing
│   │
│   └── api/
│       ├── query.py               # POST /api/query (async, source-aware)
│       ├── schema.py              # GET  /api/schema?source=
│       ├── datasources.py         # GET  /api/datasources (NEW)
│       └── history.py             # GET  /api/history
│
├── eval/
│   ├── test_queries.yaml          # 14-case test set
│   ├── run_eval.py                # eval runner
│   └── results/                   # report output dir
│
├── tests/
│   ├── test_connectors.py         # 32 cases — connector layer
│   ├── test_retrieval.py          # 20 cases
│   ├── test_agent.py              # 41 cases
│   └── test_api.py                # 16 cases
│
├── assets/                        # project logo
└── README.md
```

---

## Configuration

All configuration is read from environment variables (`.env` is auto-loaded).
See [`.env.example`](.env.example) for the full list.

### LLM provider (pick one)

| Variable | Notes |
|:---|:---|
| `OPENROUTER_API_KEY` | **Recommended.** One key for every model; names must be `vendor/model` |
| `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | Legacy per-vendor keys, only used if `OPENROUTER_API_KEY` is empty |

### Models

| Variable | Default | Purpose |
|:---|:---|:---|
| `DEFAULT_CHEAP_MODEL` | `deepseek/deepseek-chat` | Simple queries / straightforward aggregation |
| `DEFAULT_STRONG_MODEL` | `anthropic/claude-sonnet-4` | Multi-join / exploration / retry escalation |

### Embeddings (three backends, auto-selected)

| Variable | Behavior |
|:---|:---|
| `EMBEDDING_MODEL=openai/text-embedding-3-large` | Routes through OpenRouter (or direct OpenAI if only that key is set) |
| `EMBEDDING_MODEL=text-embedding-3-small` | Direct OpenAI |
| `EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5` | Local sentence-transformers (`pip install -e ".[local-embed]"`) |
| `EMBEDDING_PROVIDER` | `auto` (default) / `openai` / `openrouter` / `local` |
| `EMBEDDING_DIM` | `0` = auto-detect from a known-model lookup table |

> **Switching embedding models requires re-running the bootstrap.** pgvector
> columns are dim-typed, so going from 1536 → 3072 needs a DROP + CREATE.
> Just run `python -m src.retrieval.bootstrap`.

### Retrieval / self-correction

| Variable | Default | Notes |
|:---|:---|:---|
| `BM25_WEIGHT` | `0.4` | Hybrid BM25 weight |
| `VECTOR_WEIGHT` | `0.6` | Hybrid vector weight |
| `RERANK_TOP_K` | `5` | Number of tables returned by the reranker |
| `MAX_RETRY_COUNT` | `3` | Self-correction retry budget |
| `SQL_TIMEOUT_SECONDS` | `30` | Per-statement `statement_timeout` |

### Data sources

| Variable | Default | Notes |
|:---|:---|:---|
| `DEFAULT_SOURCE` | _(unset → reads from YAML)_ | Override for the YAML's `default_source`; the value must match a `name:` in `config/datasources.yaml` |

`config/datasources.yaml` itself supports `${VAR:-default}` placeholders for
host/port/credentials, so per-environment overrides go in `.env`:

| Variable | Used by | Default |
|:---|:---|:---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `ecommerce_pg` connection block | `localhost` / `5432` / `Elytra` / `Elytra` / `Elytra_dev` |
| `STARROCKS_HOST` / `STARROCKS_PORT` / `STARROCKS_DB` / `STARROCKS_USER` / `STARROCKS_PASSWORD` | `ecommerce_starrocks` connection block | `localhost` / `9030` / `elytra` / `root` / `(empty)` |

---

## API Reference

### `POST /api/query`

Request:

```json
{
  "query": "Which product category had the highest sales last month?",
  "session_id": "optional-session-id",
  "source": "ecommerce_pg"
}
```

`source` is optional and falls back to `default_source` from
`config/datasources.yaml`. The dialect is automatically derived from the
connector behind the source — the legacy `dialect` field is still accepted
but ignored.

Response:

```json
{
  "success": true,
  "query": "Which product category had the highest sales last month?",
  "source": "ecommerce_pg",
  "dialect": "postgresql",
  "intent": "aggregation",
  "generated_sql": "SELECT category_l1, SUM(total_amount) AS total_sales FROM dwd_order_detail ...",
  "result": [
    {"category_l1": "电子产品", "total_sales": 1523400.00}
  ],
  "visualization_hint": "bar_chart",
  "final_answer": "Query succeeded; returned 1 row.",
  "model_used": "deepseek/deepseek-chat",
  "retry_count": 0,
  "latency_ms": 1240,
  "token_count": 856,
  "error": null
}
```

### `GET /api/datasources`

Lists every connector registered with `ConnectorRegistry`:

```json
{
  "datasources": [
    {
      "name": "ecommerce_pg",
      "dialect": "postgresql",
      "description": "E-commerce simulated warehouse (ODS / DWD / DWS)",
      "connected": true,
      "table_count": 13,
      "is_default": true
    },
    {
      "name": "tpch_duckdb",
      "dialect": "duckdb",
      "description": "TPC-H standard test dataset",
      "connected": true,
      "table_count": 8,
      "is_default": false
    }
  ],
  "default": "ecommerce_pg"
}
```

`connected: false` means the connector failed its startup ping — the rest of
the registry still comes up so other sources stay queryable.

### `GET /api/schema?source=<name>`

Returns the schema for one data source, grouped by warehouse layer (`ODS` /
`DWD` / `DWS`, plus `OTHER` for sources without layer prefixes). Pass
`?source=` to pick a specific source; omit it for the default. The SYSTEM
layer is always hidden.

### `GET /api/history?session_id=xxx&limit=20`

Past query runs filtered by `session_id`, ordered by `created_at desc`. `limit ∈ [1, 200]`.

Full OpenAPI schema at <http://localhost:8000/docs>.

---

## Evaluation

The test set lives in [`eval/test_queries.yaml`](eval/test_queries.yaml) (14 cases across 5 categories). Run:

```bash
python eval/run_eval.py
# Or pass parameters
python eval/run_eval.py --api-url http://localhost:8000 --filter aggregation
```

Reports land in `eval/results/<timestamp>.{json,md}`. The markdown report
annotates each metric with PASS/FAIL, breaks down by category, and includes
per-case detail.

### Verification (2026-04-06)

| Metric | Value | Target | Status |
|:---|---:|---:|:---:|
| SQL execution success rate | 92.9 % | ≥ 85 % | ✅ PASS |
| Result accuracy rate | 92.9 % | ≥ 75 % | ✅ PASS |
| Schema recall rate | 92.9 % | ≥ 80 % | ✅ PASS |
| Avg latency | 204 ms | < 5 000 ms | ✅ PASS |
| Self-correction rate | 50 % (2 retried) | informational | — |

---

## Testing

```bash
# Whole suite
.venv/bin/python -m pytest tests/

# Verbose
.venv/bin/python -m pytest tests/ -v

# One file
.venv/bin/python -m pytest tests/test_agent.py -v
```

Currently **109 / 109 passing** in ~1 s. Coverage:

- `test_connectors.py` (32 cases) — SQL safety filter migration, `ColumnMeta`/`TableMeta`/`QueryResult` data contracts, `ConnectorFactory` lazy import + dialect routing, `ConnectorRegistry` singleton + `${VAR:-default}` env-var expansion, `enrich_with_overlay` for both YAML structures
- `test_retrieval.py` (20 cases) — tokenizer, BM25, min-max normalization, `HybridRetriever` score fusion, vector-failure fallback, real data dictionary smoke test
- `test_agent.py` (41 cases) — SQL safety filter, model routing (every branch), node behavior (with stub-connector injection), full graph end-to-end (success / retry-then-success / retry exhaustion / clarification short-circuit)
- `test_api.py` (16 cases) — `/healthz`, `/api/query` (success / failure / explicit source / unknown source / empty / agent crash 500), `/api/datasources`, `/api/schema?source=`, `/api/history`

The tests do not depend on a real database or LLM — every connector is replaced with an in-memory stub injected into the registry, so the whole suite runs in about a second locally.

---

## Roadmap

Delivered (v0.2.0):

- [x] **Multi-source abstraction layer** — `DataSourceConnector` async ABC; PG / DuckDB / StarRocks engines; YAML-driven config
- [x] **TPC-H + Brazilian E-Commerce datasets** — DuckDB built-in dbgen + Kaggle CSV loader
- [x] **Dialect-aware SQL generation** — `DIALECT_INSTRUCTIONS` switches syntax rules per target engine
- [x] **asyncpg connection pool** — agent hot path is fully async end-to-end

Next-phase highlights:

- [ ] **Multi-turn dialogue** — `conversation_history` + context summarization + anaphora resolution
- [ ] **Local cross-encoder reranker** — `bge-reranker-v2-m3` replacing the LLM reranker; column-level retrieval
- [ ] **SSE streaming** — `POST /api/query/stream`; UI shows the agent's thinking trace
- [ ] **Tool-use Agent** — upgrade to function-calling mode
- [ ] **Observability** — structured per-query trace, token cost tracking, error classification, prompt-injection hardening

---

## Contributing

PRs welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, code style, and commit conventions.

Bug reports and feature requests go to [GitHub Issues](https://github.com/shuheng-mo/Elytra/issues).

---

## License

[MIT](LICENSE) © shuheng-mo

---

<div align="center">

<img src="assets/elytra-logo-hex-icon.svg" width="48" alt="Elytra" />

**[⬆ Back to top](#elytra)**

</div>
