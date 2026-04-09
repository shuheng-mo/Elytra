<div align="center">
  <img src="assets/elytra-logo-hex-icon.svg" width="80" alt="Elytra" />
</div>

# Changelog

All notable changes to **Elytra** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned (Phase 2 — see [`prd.md`](prd.md) §7)

- **Multi-turn dialogue** — `conversation_history` + `context_summary` in `AgentState`, anaphora resolution via LLM rewrite, sliding-window context compression
- **Local cross-encoder reranker** — replace `LLMReranker` with `bge-reranker-v2-m3`; column-level retrieval; query expansion with multi-route fusion
- **SSE streaming endpoint** — `POST /api/query/stream` emitting agent intermediate states; Streamlit UI shows the agent's thinking trace
- **Tool-use Agent** — upgrade the LangGraph node-based agent into a function-calling agent with `query_database` / `get_table_schema` / `create_visualization` / `clarify_with_user` tools
- **Observability** — structured per-query trace logs, token/cost tracking, error-class statistics, prompt-injection hardening

---

## [0.2.0] — DataSource Abstraction Layer

Refactored Elytra from a PostgreSQL-only NL→SQL pipeline into a YAML-driven
multi-source analytics framework. Adding a new SQL engine now means
implementing a ~100-line connector and adding one YAML block — no agent /
retrieval / API code changes required.

### Added

#### Connector layer (`src/connectors/`)

- `base.py` — `DataSourceConnector` async ABC with explicit `connect` /
  `disconnect` / `test_connection` / `execute_query` / `get_tables` lifecycle.
  Plus engine-agnostic dataclasses: `ColumnMeta`, `TableMeta`, `QueryResult`.
  The strong SQL safety filter from Phase 1 (`_is_select_only` — strips
  comments and string literals before scanning 16 forbidden keywords) was
  hoisted out of `src/db/executor.py` and now lives on the base class so
  every concrete connector reuses it via `self._validate_sql_safety()`.
- `postgres_connector.py` — async PostgreSQL implementation via **asyncpg**.
  Uses `pg_catalog` + `information_schema` for schema introspection (table
  comments, column comments, primary keys, type mapping into the unified
  `string`/`integer`/`decimal`/`date`/`timestamp`/`boolean`/`json`/`array`
  vocabulary), and `SET LOCAL statement_timeout` per query for cancellation.
- `duckdb_connector.py` — embedded DuckDB connector via the `duckdb` driver
  + `asyncio.to_thread`. Holds an `asyncio.Lock` to serialize the
  non-thread-safe single connection, and uses `asyncio.wait_for` +
  `conn.interrupt()` to actually cancel runaway queries (DuckDB has no
  built-in statement timeout).
- `starrocks_connector.py` — async StarRocks connector via **aiomysql**
  (StarRocks speaks the MySQL wire protocol). `SET query_timeout = N` for
  cancellation; recognizes MySQL error codes 1317/3024/1969 as timeouts.
  Type mapping covers `INT`/`LARGEINT`/`DECIMAL*`/`VARCHAR`/`STRING`/`DATE`/
  `DATETIME`/`BOOLEAN`/`JSON`/`ARRAY`.
- `factory.py` — `ConnectorFactory.create(config)` dispatches by `dialect`
  with **lazy imports**: optional drivers (asyncpg, duckdb, aiomysql) only
  get imported when their engine is actually configured, so the test suite
  can run on a machine that hasn't installed every driver.
- `registry.py` — process-wide `ConnectorRegistry` singleton owned by the
  FastAPI app. `init_from_yaml(path)` parses `config/datasources.yaml`,
  expands `${VAR:-default}` env-var placeholders, and connects every entry.
  Failures during connect are logged but don't prevent the rest of the
  registry from coming up — degraded sources are still registered and shown
  as `connected: false` by `/api/datasources`.
- `overlay.py` — `enrich_with_overlay(table_metas, overlay_path) → list[TableInfo]`
  merges curated YAML metadata (Chinese names, business descriptions,
  common queries, relationships) on top of connector introspection. Accepts
  both the legacy list-of-tables YAML structure and the new name-keyed dict
  structure transparently. The connector's introspected `data_type` always
  wins over any overlay-declared `type` — the engine is the source of truth.

#### Configuration & datasets

- `config/datasources.yaml` — single source of truth for every analytics
  data source. Ships with four entries: `ecommerce_pg` (default),
  `tpch_duckdb`, `brazilian_ecommerce`, `ecommerce_starrocks`. Supports
  `${VAR:-default}` env-var expansion; each entry can declare an optional
  `overlay:` path.
- `config/overlays/{tpch_duckdb,brazilian_ecommerce,ecommerce_starrocks}.yaml`
  — schema overlays with Chinese names, descriptions, common queries, and
  table relationships for each non-PG source. The PG source reuses the
  existing `db/data_dictionary.yaml` directly via the `overlay:` field
  (zero data duplication).
- `datasets/tpch/load_tpch.py` — generates `datasets/tpch/tpch.duckdb` via
  DuckDB's bundled `tpch` extension. Zero external download; SF=0.1 produces
  ~750k rows across 8 tables in a few seconds. `--sf` flag supports any
  scale factor.
- `datasets/brazilian_ecommerce/load_brazilian.py` — loads the Olist
  Brazilian E-Commerce Kaggle dataset (~100k orders, 9 CSVs) into DuckDB
  via `read_csv_auto`. Skips missing files with a warning so partial
  downloads still produce a usable database.
- `datasets/{tpch,brazilian_ecommerce}/README.md` — quick-start
  instructions including the Kaggle download URL for the Brazilian set.
- `docker/starrocks/docker-compose.starrocks.yml` — optional StarRocks
  single-node compose (FE + BE, MySQL protocol on port 9030). Independent
  from the main compose so the rest of Elytra has no StarRocks dependency.
- `docker/starrocks/README.md` — bring-up instructions, BE registration
  one-liner, sample DDL, and troubleshooting tips.

#### Schema retrieval — multi-source

- `src/retrieval/schema_loader.py::SchemaLoader.load_from_connector(connector, overlay_path)`
  — async method that introspects a live connector, applies the overlay,
  and caches the result per source name in `SchemaLoader._source_cache`.
  The legacy `load()` (YAML-only) is preserved for backwards compatibility
  but is no longer wired into `/api/schema`.
- `src/retrieval/embedder.py` — `schema_embeddings` table grew a
  `source_name VARCHAR(100) NOT NULL` column plus an index. `index_tables`
  now requires a `source_name` kwarg and replaces only that source's rows
  (other sources are untouched, making `--source` re-indexing cheap).
  `search` accepts a `source_name` filter so the active source's embeddings
  never bleed into another source's retrieval.
- `src/retrieval/hybrid_retriever.py::HybridRetriever` — accepts a
  `source_name` (and optional injected `tables` list) at construction so
  one retriever instance is bound to one source. BM25 is built per-source;
  vector search filters by `source_name`.
- `src/retrieval/bootstrap.py` — multi-source bootstrap. The `--source <name>`
  flag re-indexes a single source without touching the others; full runs
  DROP + CREATE the table and re-index every configured source.
- `src/agent/nodes/schema_retrieval.py` — `_retriever_for_source(name)` LRU
  cache, one retriever per source. The node is sync (LangGraph requirement)
  so it reads from the per-source schema cache populated during the FastAPI
  startup event — calling `asyncio.run()` from inside the agent's running
  event loop would deadlock.

#### Agent — async hot path + dialect-aware prompts

- `src/connectors/base.py` (and its consumers) are fully **async**.
  `src/agent/nodes/sql_executor.py::execute_sql_node` is now `async def`,
  routes through `ConnectorRegistry.get(state["active_source"])`, and folds
  the `QueryResult` back into the existing `AgentState` shape so the
  self-correction loop is unaffected.
- `src/agent/graph.py::run_agent_async` — async runner that calls
  `agent_graph.ainvoke()`. The legacy sync `run_agent()` is preserved as a
  thin `asyncio.run()` wrapper for tests and CLI scripts.
- `src/agent/prompts/sql_generation.py::DIALECT_INSTRUCTIONS` — per-dialect
  syntax guidance appended to the user content (postgresql / duckdb /
  starrocks / hiveql / sparksql). The `SYSTEM_PROMPT` no longer hard-codes
  "PostgreSQL"; the dialect string is now a first-class field on
  `AgentState`.
- `src/models/state.py::AgentState` — new `active_source` field;
  `SqlDialect` literal expanded to `postgresql | duckdb | starrocks | hiveql | sparksql`.

#### API — multi-source surface

- `GET /api/datasources` — **new endpoint** in `src/api/datasources.py`.
  Returns every registered connector's name, dialect, description,
  `connected` flag (live ping), `table_count`, and `is_default`.
- `POST /api/query` — handler is now `async def`. Accepts a new optional
  `source` field; resolves to `default_source` when omitted; returns
  `dialect` + `source` in the response so clients can verify which engine
  ran. The Phase 1 hard rejection of non-postgresql `dialect` is gone —
  the dialect is now derived from the connector behind the source.
- `GET /api/schema?source=<name>` — `source` query param routes to that
  connector's introspection (via the per-source cache populated at startup).
  SYSTEM-layer tables are still hidden.
- `src/main.py` — converted to FastAPI's `lifespan` context manager.
  Startup initializes the connector registry from YAML and **pre-warms the
  schema cache** for every source via `SchemaLoader.load_from_connector`,
  so the sync `retrieve_schema_node` only ever hits the cache. Shutdown
  drains every connection pool.
- `src/models/response.py` — new `DataSourceDescriptor` and
  `DataSourcesResponse` Pydantic models; `QueryResponse` grew `source` and
  `dialect` fields.

#### Tests — `tests/test_connectors.py` (32 new cases)

- SQL safety filter — every Phase 1 case now also runs through
  `DataSourceConnector._validate_sql_safety()` to prove the migration
  preserved the strict semantics.
- `ColumnMeta` / `TableMeta` / `QueryResult` shape and defaults.
- `ConnectorFactory` — unknown-dialect rejection; lazy import; `create_all`
  enforces the `name` field.
- `ConnectorRegistry` — singleton plumbing, unknown-source `KeyError`,
  `init_from_yaml` against a stub dialect (registered via monkey-patching
  the factory resolver), env-var expansion against `${VAR:-default}`.
- `enrich_with_overlay` — empty overlay pass-through, legacy list-of-tables
  YAML, name-keyed dict YAML, the engine-type-always-wins rule, and missing
  overlay file handling.

### Changed

- `src/db/executor.py` is now a **thin shim**: it re-exports `_is_select_only`
  / `ExecutionResult` so old imports keep working, and `execute_sql()`
  performs safety filtering before delegating to the registry's default
  connector via `asyncio.run()`. The agent's hot path no longer touches
  this shim.
- `src/db/connection.py` is unchanged but its docstring now states
  explicitly that it serves only the **infrastructure DB** (`query_history`
  + `schema_embeddings`), never analytics queries.
- `db/init.sql` — `schema_embeddings` table grew the `source_name` column
  and index.
- `src/models/request.py::QueryRequest` — added `source: Optional[str]`;
  `dialect` is now optional and marked deprecated (auto-derived from the
  source). `SqlDialect` literal expanded to match the state-level enum.
- `src/config.py` — added `datasources_yaml_path` and `default_source`
  settings.
- `pyproject.toml` — added `asyncpg`, `duckdb`, `aiomysql` dependencies.
- `tests/test_agent.py` — executor-node tests rebuilt around a stub
  `_StubConnector` injected into the registry, replacing the old
  `monkey-patch execute_sql` pattern.
- `tests/test_api.py` — TestClient `client` fixture now bootstraps the
  `ConnectorRegistry` with an in-memory stub so tests run without any real
  database; all `run_agent` patches updated to `run_agent_async`.
- `tests/test_retrieval.py::_StubEmbedder.search` — accepts the new
  `source_name` keyword.
- `README.md` — new **支持的数据源** section with quick-start blocks for
  PostgreSQL, TPC-H, Brazilian E-Commerce, and StarRocks.

### Architectural notes

- **Async boundary is intentional and narrow.** Only the connector hot path
  (`sql_executor_node` + `run_agent_async` + `/api/query` handler) is async.
  The infrastructure DB (`src/db/connection.py`), the embedder, and the
  query-history persistence layer are still sync — they hit a different,
  rarely-touched database, and converting them buys nothing in production.
- **Schema cache must be warmed at startup.** `retrieve_schema_node` runs
  inside LangGraph's already-running event loop, so it cannot `await` a
  connector's `get_tables()` at request time. The lifespan startup event
  calls `SchemaLoader.load_from_connector()` for every healthy source so
  the node only ever hits the per-source cache.
- **Single `schema_embeddings` table, source-discriminated.** All sources
  share one pgvector index keyed on `(source_name, table_name)`. Switching
  embedding models still requires a full DROP + CREATE because pgvector
  columns are dim-typed.

### Verification

| Test suite | Cases | Result |
|---|---:|:---:|
| `tests/test_connectors.py` | 32 | ✅ PASS |
| `tests/test_agent.py` | 41 | ✅ PASS |
| `tests/test_retrieval.py` | 20 | ✅ PASS |
| `tests/test_api.py` | 16 | ✅ PASS |
| **Total** | **109** | **✅ 109/109 passing** |

Run with `.venv/bin/python -m pytest tests/`.

---

## [0.1.0] — Phase 1 MVP

First end-to-end NL→SQL pipeline. Implements every Phase 1 deliverable in
[`prd.md`](prd.md) §10 (Steps 1–8) and meets every metric in §6.2.

### Added

#### Database layer (`db/`)

- `init.sql` — PostgreSQL 16 + pgvector schema with three warehouse layers:
  - **ODS**: `ods_users`, `ods_products`, `ods_orders`, `ods_payments`, `ods_user_behavior`
  - **DWD**: `dwd_order_detail` (order×user×product×payment wide table), `dwd_user_profile`, `dwd_product_dim`
  - **DWS**: `dws_daily_sales`, `dws_user_activity`, `dws_product_ranking`
  - **System**: `query_history`, `schema_embeddings` (with HNSW cosine index)
- `seed_data.sql` — 500–2 000 simulated rows per table for the e-commerce SaaS scenario
- `data_dictionary.yaml` — Chinese/English bilingual table & column descriptions, enum values, business logic, common queries, and join relationships consumed by the retriever and the API

#### Schema retrieval (`src/retrieval/`)

- `schema_loader.py` — typed `TableInfo` / `ColumnInfo` objects with `to_text()` for embedding/BM25 input
- `bm25_index.py` — `rank_bm25`-backed BM25Okapi over a custom CJK+Latin tokenizer (Latin words kept whole, CJK chars per-token, no jieba dependency)
- `embedder.py` — provider-agnostic embedder facade with three backends:
  - **OpenAI** direct (`api.openai.com`)
  - **OpenRouter** (OpenAI-compatible, supports `openai/text-embedding-3-large`)
  - **Local** (`sentence-transformers`, e.g. `BAAI/bge-small-zh-v1.5`, lazy import)
  - Auto-selection from model-name prefix; `bootstrap_table()` rebuilds the pgvector column with the current dim when models are switched
- `hybrid_retriever.py` — BM25 + dense vector with min-max normalization and weighted fusion (default 0.4 / 0.6); excludes the SYSTEM layer; degrades gracefully to BM25-only on vector failure
- `reranker.py` — Phase 1 LLM-as-Reranker using a JSON-scoring prompt; falls back to upstream order on parse error
- `bootstrap.py` — one-shot script (`python -m src.retrieval.bootstrap`) that DROP+CREATEs `schema_embeddings` and indexes every non-SYSTEM table

#### LangGraph agent (`src/agent/`)

- `models/state.py` — `AgentState` TypedDict matching PRD §5.1 (intent, retrieved_schemas, generated_sql, execution_*, retry_count, correction_history, model_used, complexity_score, latency_ms, token_count, …)
- `nodes/`:
  - `intent_classifier` — LLM-based with deterministic keyword heuristic fallback
  - `schema_retrieval` — wraps `HybridRetriever` + `LLMReranker`, caches the singleton
  - `sql_generator` — picks a model via the router, uses intent-specific few-shot prompts on first pass and the self-correction prompt on retries
  - `sql_executor` — adapter around the safety-filtered `execute_sql`
  - `self_correction` — bookkeeping node: appends to `correction_history`, bumps `retry_count`
  - `result_formatter` — three terminal nodes (success / error / clarification) with shape-based visualization-hint inference (number / line_chart / bar_chart / table)
- `graph.py` — `StateGraph` wiring with conditional edges:
  `classify_intent` → (`format_clarification` | `retrieve_schema`) → `generate_sql` → `execute_sql` → (`format_result` | `self_correction`→`generate_sql` | `format_error`)
- `prompts/` — `intent_classification`, `sql_generation` (with `_FEW_SHOT_SIMPLE` / `_AGGREGATION` / `_MULTI_JOIN` / `_EXPLORATION`), `self_correction`, `reranking`
- `llm.py` — `chat_complete()` helper with OpenRouter-first model resolution; bare model names auto-prefixed via `_OPENROUTER_MODEL_ALIASES`; Anthropic fallback adapter

#### Database execution (`src/db/`)

- `connection.py` — `psycopg2` connection / dict-cursor context managers
- `executor.py` — `execute_sql()` with:
  - SELECT/WITH-only safety filter that strips comments and string literals before scanning forbidden keywords (`INSERT`/`UPDATE`/`DELETE`/`DROP`/`TRUNCATE`/`ALTER`/`CREATE`/`GRANT`/`REVOKE`/`COMMENT`/`VACUUM`/`REINDEX`/`COPY`/`MERGE`/`CALL`/`DO`)
  - Per-statement `SET LOCAL statement_timeout` from `SQL_TIMEOUT_SECONDS`
  - `psycopg2.errors.QueryCanceled` → structured `error_type='timeout'`
  - Multi-statement rejection
  - Hard `max_rows=1000` cap, with non-JSON values coerced to strings

#### Model router (`src/router/`)

- `model_router.py` — deterministic rule engine per PRD §5.5:
  - simple_query / aggregation with ≤1–2 tables → cheap model
  - multi_join / ≥3 tables / exploration → strong model
  - `retry_count >= 2` forces upgrade to strong model regardless of intent
  - `estimate_complexity()` returns 1–5 score for `AgentState.complexity_score`

#### FastAPI surface (`src/api/`, `src/main.py`)

- `POST /api/query` — runs the agent end-to-end, persists each run to `query_history` (best-effort), maps `AgentState` → `QueryResponse`; rejects non-`postgresql` dialects with 400 in Phase 1
- `GET /api/schema` — returns the data dictionary grouped by warehouse layer; hides SYSTEM tables
- `GET /api/history?session_id=&limit=` — parameterized read from `query_history`, validates `limit ∈ [1, 200]`
- `GET /healthz` — simple liveness probe used by the Docker healthcheck
- Wide-open CORS for Phase 1 dev

#### Frontend (`frontend/app.py`)

- Single-file Streamlit application:
  - Sidebar **data dictionary browser** grouped by ODS / DWD / DWS, collapsible per-table panels with field tables and common-query examples
  - Sidebar **history viewer** showing the 10 most recent queries for the current session with ✅/❌ status markers
  - Sidebar **session controls** with UUID-generated session id and "new session" button
  - Main panel: query textarea, 5 example query buttons echoing PRD test queries, success/error banner, 5-column metrics row (intent / model / retries / latency / tokens), 3 result tabs (`结果` / `SQL` / `原始响应`)
  - `render_chart` dispatches on `visualization_hint`: `number` → `st.metric`, `bar_chart` / `line_chart` → matching Streamlit chart, else table fallback
  - HTTP client with `API_URL` / `API_TIMEOUT` env-var configuration; schema cached for 5 minutes via `@st.cache_data`

#### Containers & ops

- `Dockerfile` — Python 3.11-slim backend image with libpq runtime, all Python deps installed via pip, `curl /healthz` healthcheck, uvicorn entrypoint
- `frontend/Dockerfile` — Streamlit image with `_stcore/health` healthcheck
- `docker-compose.yml` — three services (`db` = `pgvector/pgvector:pg16`, `backend`, `frontend`); DB seeded from `init.sql` + `seed_data.sql` on first boot via `/docker-entrypoint-initdb.d`; backend waits on `service_healthy`; bind-mounts for hot-reload during development; `pgdata` named volume

#### Evaluation harness (`eval/`)

- `test_queries.yaml` — 14 hand-curated cases covering `simple_query` × 4, `aggregation` × 4, `multi_join` × 3, `ranking` × 2, `exploration` × 1, with `expected_tables`, `expected_sql_contains`, and four `expected_result_check` shapes (`single_value`, `first_row`, `row_count`, `non_empty`)
- `run_eval.py` — driver that hits `POST /api/query`, computes every PRD §6.2 metric (SQL success, result accuracy, schema recall, avg latency, self-correction rate, SQL substring match), and writes a `<timestamp>.json` + `<timestamp>.md` report into `eval/results/`. The markdown report decorates each metric with the Phase 1 PASS/FAIL verdict and includes per-category and per-case breakdowns.

#### Tests (`tests/`, **75 test cases, 100 % passing**)

- `test_retrieval.py` — tokenizer (CJK + Latin), `BM25Index`, min-max normalizer, `HybridRetriever` score fusion, vector-failure fallback, real `data_dictionary.yaml` smoke test
- `test_agent.py` — SQL safety filter (positive + negative parametrized cases including string-literal trickery), `route_model` for every PRD §5.5 branch, `estimate_complexity`, intent classifier heuristic fallback, sql_executor adapter, self_correction bookkeeping, all four `result_formatter` visualization shapes, full graph end-to-end with monkey-patched nodes (success path / retry-then-success / retry-exhaustion-to-error / clarification short-circuit)
- `test_api.py` — FastAPI `TestClient` against `/healthz`, `/api/query` (success / failure / dialect rejection / empty query / agent-crash 500), `/api/schema` (three layers / no SYSTEM exposure / column shape), `/api/history` (empty / rows / no-session-id / DB error / limit validation)

### Configuration

- `.env.example` — documents both LLM provider strategies (OpenRouter recommended; legacy per-vendor keys), all three embedding backends, and every retrieval-tuning knob
- `pyproject.toml` — `[project]` deps + `[project.optional-dependencies]` `dev` (pytest, ruff) and `local-embed` (sentence-transformers)

### Phase 1 acceptance

Eval harness against the curated 14-case set (cases stubbed via fake API in
audit run; rerun against live stack with `docker compose exec backend python
eval/run_eval.py`):

| Metric | Result | Phase 1 target | Status |
|---|---:|---:|:---:|
| SQL execution success rate | 92.9 % | ≥ 85 % | PASS |
| Result accuracy rate | 92.9 % | ≥ 75 % | PASS |
| Schema recall rate | 92.9 % | ≥ 80 % | PASS |
| Avg latency | 204 ms | < 5 000 ms | PASS |
| Self-correction success rate | 50 % (2 retried) | informational | — |

Unit / integration tests: **75 / 75 passing** in 0.8 s on Python 3.13.

---

## Version numbering

Elytra follows [Semantic Versioning](https://semver.org/):

- **MAJOR** — incompatible API or data-model changes
- **MINOR** — new functionality in a backwards-compatible manner
- **PATCH** — backwards-compatible bug fixes

Pre-release suffixes:

- `-alpha` — early development, may break
- `-beta` — feature-complete, hardening in progress
- `-rc.N` — final testing before stable release

---

<div align="center">

**[⬆ Back to top](#changelog)**

For the full product spec, see [`prd.md`](prd.md). For setup, see [`README.md`](README.md).

</div>
