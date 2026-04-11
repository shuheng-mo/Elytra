<div align="center">
  <img src="assets/elytra-logo-hex-icon.svg" width="80" alt="Elytra" />
</div>

# Changelog

All notable changes to **Elytra** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned

- **Multi-turn dialogue** — `conversation_history` + `context_summary` in `AgentState`, anaphora resolution via LLM rewrite, sliding-window context compression
- **Local cross-encoder reranker** — replace `LLMReranker` with `bge-reranker-v2-m3`; column-level retrieval; query expansion with multi-route fusion
- **Tool-use Agent** — upgrade the LangGraph node-based agent into a function-calling agent with `query_database` / `get_table_schema` / `create_visualization` / `clarify_with_user` tools
- **Observability** — structured per-query trace logs, error-class statistics, prompt-injection hardening
- **Daily query trend** — add a `time_series` field to `AuditStatsResponse` and wire up the placeholder line chart in the React audit dashboard

---

## [0.4.0] — React Frontend, Cost Tracking & Runtime Connector Management

A full React SPA replaces Streamlit as the default frontend, with Streamlit
preserved as a docker compose fallback profile for backend cross-checking.
Adds runtime connector management, per-query cost estimation, and Excel /
CSV export across every data page.

### Added

#### React frontend (`frontend-react/`)

- **Vite + React 18 + Tailwind + shadcn/ui** SPA. Six pages: Query, Schema
  Explorer, History, Audit, Data Connectors, Settings. Routing is plain
  `useState` (no react-router) per the existing minimalism convention.
- **Real-time agent timeline** — `useQuery` hook subscribes to
  `WebSocket /ws/task/{id}`, maps each `progress` event into a step on the
  timeline. Each step is click-expandable to show the agent's reasoning
  log lines forwarded from `astream_events(version="v2")` (Claude-style
  inline reasoning UX).
- **`echarts-for-react`** consumes the backend `chart_spec` ECharts JSON
  zero-adapter — a deliberate trade-off vs. the original spec's Recharts
  plan. Removes the per-shape mapping layer and pixel-aligns with the
  Streamlit fallback. PostgreSQL `DECIMAL`-as-string values are coerced
  to numbers in `ChartRenderer.normalizeChartSpec`.
- **Settings page** — theme (dark / light), accent color preset, user
  identity switch (analyst / operator / admin → permission filter),
  default query mode (sync / async), session id reset. State persisted
  to `localStorage` via a `SettingsProvider` context.
- **Data Connectors page** — lists every registered connector with
  dialect-specific official icons (inline simple-icons SVG paths, no npm
  dep), filter by dialect, "add new" form driven by
  `GET /api/datasources/types`, and a delete dialog that distinguishes
  user-managed (permanent) from primary (runtime-only) entries.
- **Multi-format export** — Schema, History, and Audit pages each have
  "导出 Excel" + "导出 CSV" buttons via `src/lib/export.js` (SheetJS
  community edition). Settings has a "session bundle" export that fetches
  schema + history + audit live from the API and packs them into one
  multi-sheet xlsx scoped to the current session id. CSV writes a UTF-8
  BOM so Excel opens Chinese cleanly.
- Multi-stage Dockerfile (node build → nginx serve `:3000`) with
  `nginx.conf` reverse-proxying `/api/` and `/ws/` to the backend so the
  browser always sees same-origin requests.

#### Cost estimation (`src/agent/cost.py`)

- New `MODEL_PRICING_PER_1M` table covering DeepSeek / Anthropic / OpenAI /
  Gemini / Llama / Qwen, plus a `_DEFAULT_BLENDED_RATE_PER_1M = $2.00`
  fallback for unknown models.
- `estimate_cost_usd(model, token_count) -> float` uses a blended rate
  (rough 3:1 input:output) because `AgentState.token_count` is a single
  cumulative number, not split by direction.
- `_persist_history()` in `src/api/query.py` now computes and writes
  `estimated_cost` for every successful run. The column already existed
  on `query_history` but was never populated, so
  `audit_stats.total_cost_usd` used to always return 0.
- `GET /api/audit/stats` aggregates `SUM(estimated_cost)` and per-model
  cost.

#### Runtime connector management (`src/api/datasources.py`, `src/connectors/registry.py`)

- `GET /api/datasources/types` — returns the field schema (dialect →
  required / optional fields with types and descriptions) used by the
  React form to render a dynamic "add data source" UI.
- `POST /api/datasources` — validates the request, dispatches via
  `ConnectorFactory`, performs a live connection ping, registers the
  connector in the singleton, pre-warms its schema cache, and persists
  the entry to `config/datasources.local.yaml` (user layer, gitignored,
  written `0600`). Loaded on startup if present and merged into the
  registry alongside the primary `config/datasources.yaml` entries.
- `DELETE /api/datasources/{name}` — disconnects and removes the
  connector from the in-memory registry. User-managed entries are also
  stripped from `datasources.local.yaml`. Primary entries are removed
  runtime-only — the next backend restart re-loads them from
  `config/datasources.yaml`.
- `ConnectorRegistry` tracks `_user_managed: set[str]` to distinguish
  the two layers; `_persist_user_layer()` writes the user YAML; the
  primary file is never touched at runtime.

### Changed

#### Async task manager — persistence gap fix

- `src/tasks/manager.py::execute_with_progress` previously consumed
  `astream_events` to feed WebSocket progress; on the happy path the
  outer `run_fn()` body (which contained `_persist_history`) was
  skipped, so async-mode queries **never** wrote to `query_history`.
  Audit stats and history pages quietly missed every async query.
- Added a `persist_fn` callback parameter. When the astream loop
  successfully captures the final state, `persist_fn(state)` is called
  separately so persistence and progress streaming stay decoupled.
- `src/api/query_async.py` passes `persist_fn=_persist`, where
  `_persist` calls the same `_persist_history()` used by the sync
  endpoint. Sync and async writes are now identical.

#### Docker Compose

- New `frontend-react` service (port 3000), `depends_on: backend`,
  multi-stage build → nginx.
- Existing `frontend` (Streamlit) service moved under
  `profiles: ["fallback"]`. Default `docker compose up` only brings up
  `db + backend + frontend-react`. Use
  `docker compose --profile fallback up` to additionally expose
  Streamlit on `:8501` for cross-checking.

### Verification

| Test suite | Cases | Result |
|---|---:|:---:|
| `tests/test_*.py` (full backend suite) | 173 | ✅ PASS |

E2E verified on a live PG + LLM + React stack:

- Sync and async queries both render the real-time agent timeline; each
  step is click-expandable to show the underlying astream events.
- Schema / History / Audit Excel + CSV exports open correctly with
  Chinese characters in Excel and Numbers.
- Settings session bundle xlsx contains: Bundle Info, Schema Summary,
  Schema layer sheets, History (session-scoped), Audit Summary, and
  per-dimension Audit sheets.
- `POST → DELETE → POST` cycle works for both user-managed and primary
  connectors; primary deletes are runtime-only and restored on backend
  restart, user-managed deletes flush `datasources.local.yaml`.
- After running mixed queries through both endpoints,
  `GET /api/audit/stats?days=7` returns non-zero `total_cost_usd` and a
  per-model cost breakdown that matches `query_history.estimated_cost`.

---

## [0.3.0] — Async Tasks, Permissions, Audit & NL2Chart

Four incremental features bringing Elytra closer to production readiness:
async task execution, role-based access control, full query audit trail,
and automatic chart generation.

### Added

#### Async task architecture (`src/tasks/`, `src/api/query_async.py`, `src/api/ws.py`)

- `TaskManager` — in-memory async task manager with `asyncio.Semaphore`
  concurrency control (configurable via `MAX_CONCURRENT_TASKS`, default 5)
  and subscriber-based progress push via `asyncio.Queue`.
- `POST /api/query/async` — accepts the same `QueryRequest` body as
  `/api/query`, returns `{"task_id", "status", "ws_url"}` immediately.
  The agent runs in a background `asyncio.Task`.
- `GET /api/task/{task_id}` — poll task status, progress percentage,
  current step, and final result (when complete).
- `WebSocket /ws/task/{task_id}` — real-time event stream. Pushes
  `{"type": "progress", "step": "generating_sql", "pct": 60}` after each
  LangGraph node completes; sends `{"type": "complete", ...}` on finish.
- `execute_with_progress()` uses LangGraph `astream_events(version="v2")`
  to map node completions to progress percentages without modifying any
  node code.

#### Permission & multi-tenant isolation (`src/auth/`, `config/permissions.yaml`)

- `config/permissions.yaml` — YAML-driven role definitions with three
  built-in roles: `analyst` (DWD+DWS), `operator` (DWS only), `admin`
  (all tables). Each role declares `allowed_tables` (glob patterns like
  `dws_*`), `denied_columns` (per-table field blacklist), and
  `max_result_rows`.
- `PermissionFilter` class — loads the YAML config; resolves `user_id` →
  role; filters `retrieved_schemas` by allowed tables and denied columns;
  enforces SQL `LIMIT` clamping. Degrades gracefully when the config file
  is missing (all-access).
- `filter_by_permission_node` — new LangGraph node inserted between
  `retrieve_schema` and `generate_sql`. If all tables are filtered out,
  short-circuits to clarification with a permission-denied message.
- `QueryRequest` gained an optional `user_id` field; `QueryResponse`
  gained `user_role` and `tables_filtered`.

#### SQL audit log & replay (`src/api/audit.py`, `db/migrations/001_extend_query_history.sql`)

- `query_history` table extended with 9 new nullable columns: `user_id`,
  `user_role`, `source_name`, `retrieved_tables` (JSON), `correction_history_json`
  (JSONB), `result_row_count`, `result_hash` (SHA-256 of first 100 rows),
  `token_input`, `token_output`.
- `_persist_history()` now writes all audit fields including computed
  `result_hash` via `_compute_result_hash()`.
- `POST /api/replay/{history_id}` — loads a historical query, re-executes
  it through the full agent pipeline, computes a new `result_hash`, and
  compares with the original. Returns `result_match: bool` and
  `diff_summary` when hashes differ.
- `GET /api/audit/stats?days=N` — aggregate statistics over the last N
  days: total queries, success rate, average latency, cost, breakdowns by
  model / intent / source / user.
- `db/migrations/001_extend_query_history.sql` — safe `ALTER TABLE ADD
  COLUMN IF NOT EXISTS` migration for existing databases.

#### NL2Chart (`src/chart/`, `src/agent/nodes/chart_generator.py`)

- Rule-based chart type inference engine in `src/chart/inferrer.py`:
  - 1 row × 1 col → `number_card`
  - temporal + numeric → `line`
  - categorical + numeric (≤ 8 rows) → `pie`
  - categorical + numeric (> 8 rows) → `bar`
  - numeric + numeric → `scatter`
  - temporal + categorical + numeric → `multi_line`
  - Handles PostgreSQL `DECIMAL` columns returned as strings by psycopg2.
- `src/chart/echarts_builder.py` — builds ECharts-compatible JSON option
  objects. Truncates to sane limits (20 bars, 200 line points, 10 pie
  slices) to keep response size manageable.
- `generate_chart_node` — new LangGraph node after `format_result` on the
  success path. Writes `chart_spec` (dict or None) into `AgentState`.
- `QueryResponse` gained `chart_spec: Optional[dict]`.
- `frontend/app.py` — renders ECharts specs via `streamlit-echarts` 0.4.x;
  falls back to Phase 1 built-in Streamlit charts when `chart_spec` is
  None or the library is unavailable.

#### Architecture decisions (`README.md`)

- New "架构决策" section explaining why Celery/Redis is unnecessary
  (bottleneck is LLM API, not compute) and why YAML-driven permissions
  instead of full RBAC.

### Changed

- `src/agent/graph.py` — LangGraph topology expanded from 8 to 10 nodes.
  Two new edges: `retrieve_schema → filter_by_permission → generate_sql`
  and `format_result → generate_chart → END`.
- `src/models/state.py::AgentState` — new fields: `user_id`, `user_role`,
  `chart_spec`.
- `src/models/request.py::QueryRequest` — new optional `user_id` field.
- `src/models/response.py` — `QueryResponse` gained `user_role`,
  `tables_filtered`, `chart_spec`; `HistoryItem` gained `user_id`,
  `user_role`, `source_name`, `result_row_count`, `result_hash`; new
  models `ReplayResponse`, `AuditStatsResponse`.
- `src/api/query.py::_persist_history()` — extended INSERT from 9 to 16
  columns with full audit data.
- `src/api/history.py` — SELECT includes new audit columns.
- `src/main.py` — registers 3 new routers (async_query, ws, audit);
  initializes `TaskManager` in lifespan.
- `src/config.py` — added `permissions_yaml_path` and `max_concurrent_tasks`.
- `db/init.sql` — `query_history` table includes Phase 2+ audit columns
  from the start (for fresh installs).
- `pyproject.toml` — added `streamlit-echarts>=0.4,<0.5` dependency.
- `frontend/app.py` — `render_chart` now accepts `chart_spec` and
  delegates to `_render_echart()` when available.
- `tests/test_agent.py::_patch_all_nodes` — patches the two new nodes
  (`filter_by_permission_node`, `generate_chart_node`).
- `tests/test_api.py` — `_fake_run_agent` and `_row()` updated for new
  fields and `user_id` parameter.

### Verification

| Test suite | Cases | Result |
|---|---:|:---:|
| `tests/test_connectors.py` | 32 | ✅ PASS |
| `tests/test_retrieval.py` | 20 | ✅ PASS |
| `tests/test_agent.py` | 41 | ✅ PASS |
| `tests/test_api.py` | 16 | ✅ PASS |
| `tests/test_audit.py` | 9 | ✅ PASS |
| `tests/test_permissions.py` | 17 | ✅ PASS |
| `tests/test_tasks.py` | 10 | ✅ PASS |
| `tests/test_chart.py` | 25 | ✅ PASS |
| **Total** | **173** | **✅ 173/173 passing** |

E2E verified on live PG + LLM stack:

- Permission filtering: `demo_operator` restricted to DWS tables, `demo_admin` accesses all
- Audit log: `query_history` records user_id, user_role, source_name, result_hash, retrieved_tables
- Async tasks: `POST /api/query/async` → poll `GET /api/task/{id}` → success
- NL2Chart: categorical → pie, time series → line, single value → number_card

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
  - `asyncio.to_thread`. Holds an `asyncio.Lock` to serialize the
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
  - `schema_embeddings`), never analytics queries.
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

First end-to-end NL→SQL pipeline. Implements every Phase 1 deliverable.

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

- `models/state.py` — `AgentState` TypedDict (intent, retrieved_schemas, generated_sql, execution_*, retry_count, correction_history, model_used, complexity_score, latency_ms, token_count, …)
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

- `model_router.py` — deterministic rule engine:
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
  - Main panel: query textarea, 5 example query buttons, success/error banner, 5-column metrics row (intent / model / retries / latency / tokens), 3 result tabs (`结果` / `SQL` / `原始响应`)
  - `render_chart` dispatches on `visualization_hint`: `number` → `st.metric`, `bar_chart` / `line_chart` → matching Streamlit chart, else table fallback
  - HTTP client with `API_URL` / `API_TIMEOUT` env-var configuration; schema cached for 5 minutes via `@st.cache_data`

#### Containers & ops

- `Dockerfile` — Python 3.11-slim backend image with libpq runtime, all Python deps installed via pip, `curl /healthz` healthcheck, uvicorn entrypoint
- `frontend/Dockerfile` — Streamlit image with `_stcore/health` healthcheck
- `docker-compose.yml` — three services (`db` = `pgvector/pgvector:pg16`, `backend`, `frontend`); DB seeded from `init.sql` + `seed_data.sql` on first boot via `/docker-entrypoint-initdb.d`; backend waits on `service_healthy`; bind-mounts for hot-reload during development; `pgdata` named volume

#### Evaluation harness (`eval/`)

- `test_queries.yaml` — 14 hand-curated cases covering `simple_query` × 4, `aggregation` × 4, `multi_join` × 3, `ranking` × 2, `exploration` × 1, with `expected_tables`, `expected_sql_contains`, and four `expected_result_check` shapes (`single_value`, `first_row`, `row_count`, `non_empty`)
- `run_eval.py` — driver that hits `POST /api/query`, computes key metrics (SQL success, result accuracy, schema recall, avg latency, self-correction rate, SQL substring match), and writes a `<timestamp>.json` + `<timestamp>.md` report into `eval/results/`. The markdown report decorates each metric with the PASS/FAIL verdict and includes per-category and per-case breakdowns.

#### Tests (`tests/`, **75 test cases, 100 % passing**)

- `test_retrieval.py` — tokenizer (CJK + Latin), `BM25Index`, min-max normalizer, `HybridRetriever` score fusion, vector-failure fallback, real `data_dictionary.yaml` smoke test
- `test_agent.py` — SQL safety filter (positive + negative parametrized cases including string-literal trickery), `route_model` for every routing branch, `estimate_complexity`, intent classifier heuristic fallback, sql_executor adapter, self_correction bookkeeping, all four `result_formatter` visualization shapes, full graph end-to-end with monkey-patched nodes (success path / retry-then-success / retry-exhaustion-to-error / clarification short-circuit)
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

For setup instructions, see [`README.md`](README.md).

</div>
