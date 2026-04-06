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
- **HiveQL / SparkSQL dialects** — switch SQL prompt + grammar validation via the `dialect` request field
- **Tool-use Agent** — upgrade the LangGraph node-based agent into a function-calling agent with `query_database` / `get_table_schema` / `create_visualization` / `clarify_with_user` tools
- **Observability** — structured per-query trace logs, token/cost tracking, error-class statistics, asyncpg pool, prompt-injection hardening

---

## [0.1.0] — 2026-04-06 (Phase 1 MVP)

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

### Phase 1 acceptance — verified 2026-04-06

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
