<div align="center">
  <img src="assets/elytra-logo-hex-icon.svg" width="80" alt="Elytra" />
</div>

# Contributing to Elytra

Thank you for considering a contribution to **Elytra**! This document describes
how the project is laid out, how to set up a dev environment, and the
conventions we follow for code, tests, and commits.

If you're new to the project, the fastest way to orient yourself is:

1. Read [`README.md`](README.md) — it has the architecture diagram, setup
   instructions, and a quick tour of every module.
2. Skim [`prd.md`](prd.md) §1–§6 for the product spec and the data model.
3. Run the test suite locally (one shell command — see below) to confirm your
   environment is wired up.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Ways to Contribute](#ways-to-contribute)
- [Development Setup](#development-setup)
- [Project Layout](#project-layout)
- [Development Workflow](#development-workflow)
- [Code Style](#code-style)
- [Commit Convention](#commit-convention)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)
- [Adding to the Eval Suite](#adding-to-the-eval-suite)
- [Working with the Database](#working-with-the-database)
- [Working with LLM Providers](#working-with-llm-providers)
- [Community](#community)

---

## Code of Conduct

Be kind. We treat each other with respect regardless of background, experience
level, or opinion. Disagreements about technical decisions are normal and
welcome — disagreements about people are not. If you see something
unacceptable, please contact the maintainer at
[@shuheng-mo](https://github.com/shuheng-mo).

---

## Ways to Contribute

### Bug reports

Open an [Issue](https://github.com/shuheng-mo/Elytra/issues/new) including:

- A clear, minimal reproduction (ideally a single `curl` against `/api/query` or a code snippet against one of the modules)
- What you expected vs. what happened
- Environment: OS, Python version, whether you're running via `docker compose` or locally, which LLM provider, which embedding backend
- Relevant logs (use `LOG_LEVEL=DEBUG` if needed) and the failed `eval/results/*.md` if applicable

Before opening, please search existing Issues to avoid duplicates.

### Feature requests

Tell us **what problem you're trying to solve**, not just what feature you
want. A good template:

```markdown
**Context** — what scenario are you in?
**Pain** — what doesn't work today / what's slow / what's missing?
**Proposed solution** — your idea (optional)
**Alternatives** — anything else you considered (optional)
```

If your request is one of the items on the [Phase 2 roadmap](README.md#roadmap), say so — that helps us prioritize.

### Code contributions

We welcome PRs for:

- 🐛 **Bug fixes** — always welcome
- ✨ **New features** that fit into the [Phase 2 plan](prd.md) (§7) — please open an Issue first to align on scope
- ⚡ **Performance improvements** — bring numbers (eval report before/after)
- 📝 **Documentation** — typos, clarifications, missing examples
- ✅ **More tests** — especially eval-set additions

### Documentation

Doc PRs are first-class. Fix typos, clarify confusing passages, add diagrams,
translate, or improve example queries. The READMEs are bilingual
(`README.md` 中文 / `README_EN.md` English) — please keep both in sync when
the change is structural.

---

## Development Setup

### Prerequisites

- **Python** ≥ 3.11
- **Docker** + **Docker Compose** (for the database — local install of Postgres works too)
- **uv** (recommended) or pip
- A **LLM API key** — [OpenRouter](https://openrouter.ai/) recommended

### One-time setup

```bash
# 1. Fork on GitHub, then clone your fork
git clone https://github.com/<your-username>/Elytra.git
cd Elytra

# 2. Add upstream so you can pull new changes
git remote add upstream https://github.com/shuheng-mo/Elytra.git

# 3. Install Python deps (uv recommended)
uv sync

# Or, if you prefer pip:
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in OPENROUTER_API_KEY (or your per-vendor keys)
```

### Running the stack locally

The easiest path is `docker compose`:

```bash
docker compose up --build -d
docker compose exec backend python -m src.retrieval.bootstrap
```

If you'd rather run the backend on your host (faster reloads):

```bash
# Just the database in Docker
docker run -d --name elytra-db \
  -e POSTGRES_DB=Elytra -e POSTGRES_USER=Elytra -e POSTGRES_PASSWORD=Elytra_dev \
  -p 5432:5432 \
  -v "$PWD/db/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
  -v "$PWD/db/seed_data.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro" \
  pgvector/pgvector:pg16

# Then on the host
.venv/bin/python -m src.retrieval.bootstrap
.venv/bin/uvicorn src.main:app --reload --port 8000
.venv/bin/streamlit run frontend/app.py    # in another terminal
```

### Running the tests

```bash
.venv/bin/python -m pytest tests/ -v
```

Should print **75 / 75 passing** in well under a second. Tests do not need a
real database or LLM — every external dependency is monkey-patched.

---

## Project Layout

The codebase is organized by responsibility, not by file type. From a
contributor's perspective the most important entry points are:

| Area | Where to look |
|:---|:---|
| Add a new table to the warehouse | [`db/init.sql`](db/init.sql) + [`db/seed_data.sql`](db/seed_data.sql) + [`db/data_dictionary.yaml`](db/data_dictionary.yaml) |
| Tweak the schema retriever | [`src/retrieval/`](src/retrieval/) (BM25, embedder, hybrid_retriever, reranker) |
| Add a new agent node or change a prompt | [`src/agent/nodes/`](src/agent/nodes/) and [`src/agent/prompts/`](src/agent/prompts/) |
| Change the model routing rules | [`src/router/model_router.py`](src/router/model_router.py) |
| Add a new API endpoint | [`src/api/`](src/api/) and register it in [`src/main.py`](src/main.py) |
| Tweak the SQL safety filter | [`src/db/executor.py`](src/db/executor.py) |
| Add a new test query | [`eval/test_queries.yaml`](eval/test_queries.yaml) |
| Update the frontend | [`frontend/app.py`](frontend/app.py) (single file) |

For a full file-by-file walkthrough, see [`README.md`](README.md#project-structure).

---

## Development Workflow

### 1. Create a branch

Always work on a branch off `main`:

```bash
git checkout main
git pull upstream main
git checkout -b feature/my-awesome-feature
```

Branch naming:

| Prefix | For |
|:---|:---|
| `feature/` | New features |
| `fix/` | Bug fixes |
| `perf/` | Performance work |
| `refactor/` | Refactoring with no behavior change |
| `docs/` | Documentation only |
| `test/` | Test-only changes |
| `ci/` | CI / build / tooling |

### 2. Make your change

Keep commits focused — one logical change per commit. Don't bundle unrelated
fixes together; it makes review and reverts much harder.

### 3. Run the local checks

```bash
# Lint
.venv/bin/ruff check src tests

# Format check
.venv/bin/ruff format --check src tests

# Tests
.venv/bin/python -m pytest tests/ -v
```

If you've changed retrieval, agent prompts, or the model router, also re-run
the eval (against a real backend with a real LLM key):

```bash
docker compose exec backend python eval/run_eval.py
```

Drop the resulting markdown report into the PR description.

### 4. Stay synced with upstream

```bash
git fetch upstream
git rebase upstream/main
```

Resolve any conflicts and continue. **Never** force-push to `main`.

---

## Code Style

We use [**ruff**](https://github.com/astral-sh/ruff) for both linting and
formatting. Configuration lives in [`pyproject.toml`](pyproject.toml) under
`[tool.ruff]`. Highlights:

- **Python target**: `py311`
- **Line length**: 100 characters
- **Import order**: ruff handles it (`isort`-compatible rules)

### Conventions

- **Type hints everywhere.** Use `from __future__ import annotations` at the top of every module so you can write `list[str]` instead of `List[str]`.
- **Dataclasses or `TypedDict`** for structured data, not raw dicts. The agent state is a `TypedDict`; configuration is a frozen dataclass.
- **Docstrings on public functions and classes.** Explain *why*, not *what* — the function name and types tell you what; the docstring should tell you when to use it and what gotchas exist.
- **Small modules.** If a file is creeping past ~400 lines, that's a hint to split it.
- **No bare `except:`.** Use `except SomeException:` or, if you really need it, `except Exception as exc: # noqa: BLE001` with a comment justifying the broad catch (e.g. graceful fallback).
- **Logging via `logging.getLogger(__name__)`**, not `print`.

### Things we deliberately *don't* do

- **No mocks for the database in eval.** The eval suite hits a real `pgvector` instance because that's the only way to catch SQL/migration drift. Unit tests in `tests/` *do* use stubs, by design — those exist for fast feedback.
- **No silent fallback that hides bugs.** When a fallback path fires (e.g. LLM rerank fails → use upstream order), it must `logger.warning(...)` so we notice.
- **No premature abstraction.** If you're tempted to introduce an interface for a single implementation, don't.

---

## Commit Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/) so the
changelog stays readable.

```text
<type>(<scope>): <subject>

<body>

<footer>
```

### Type

| Type | Use for |
|:---|:---|
| `feat` | New user-visible feature |
| `fix` | Bug fix |
| `perf` | Performance improvement (no behavior change) |
| `refactor` | Code restructuring (no behavior change) |
| `docs` | Documentation only |
| `test` | Tests only |
| `build` | Build system / dependencies |
| `ci` | CI configuration |
| `chore` | Tooling / housekeeping |

### Scope (optional but encouraged)

Common scopes in this repo:

- `agent` / `nodes` / `prompts` — LangGraph layer
- `retrieval` / `bm25` / `embedder` / `reranker` — retrieval layer
- `api` / `query` / `schema` / `history` — FastAPI surface
- `db` / `executor` — database layer
- `router` — model router
- `frontend` — Streamlit
- `eval` — evaluation harness
- `docker` — compose / Dockerfiles
- `deps` — dependency bumps

### Subject

- Use the imperative mood: `add` not `added` / `adds`
- Don't capitalize the first letter
- No trailing period
- Aim for ≤ 50 characters

### Examples

```text
feat(retrieval): add local sentence-transformers backend

Wires up a third Embedder backend driven by sentence-transformers,
auto-selected when EMBEDDING_MODEL starts with "BAAI/" or contains
"bge". Lazy-imports the dependency so users without local embedding
don't pay the install cost.

The new backend joins the existing OpenAI direct and OpenRouter
backends behind the same Embedder facade — no API change.

Closes #42
```

```text
fix(executor): allow string literals containing forbidden keywords

The safety filter was rejecting `SELECT 'DROP TABLE users' AS msg`
because it scanned the raw SQL for forbidden tokens. Now we strip
string literals (and comments) before scanning, so the literal is
treated as data, not a command.

Adds a test for the previously-broken case.
```

```text
docs(readme): document Phase 1 eval results
```

---

## Pull Request Process

### Before opening

- ✅ All tests pass locally (`pytest tests/`)
- ✅ `ruff check` is clean
- ✅ New features have unit tests; new prompts/retrieval logic have eval coverage
- ✅ Public interface changes are reflected in [`README.md`](README.md), [`README_EN.md`](README_EN.md), and (if user-visible) [`CHANGELOG.md`](CHANGELOG.md)
- ✅ Commits follow the [convention](#commit-convention)
- ✅ Your branch is rebased onto `upstream/main`

### Opening the PR

```bash
git push origin feature/my-awesome-feature
```

Then create the PR on GitHub. Use this template:

```markdown
## Summary

What does this change and why?

## Motivation

Background and context. If there's an Issue, link it: `Closes #123`.

## Changes

- Bullet list of the actual code changes
- One bullet per logically distinct change

## Test plan

- [ ] Unit tests added / updated
- [ ] `pytest tests/` passes locally
- [ ] (If retrieval/agent/router was touched) eval re-run, results below
- [ ] Manual smoke test in Streamlit (if user-facing)

## Eval results

(Paste the markdown summary table from `eval/results/<timestamp>.md` if relevant.)

## Screenshots

(For UI changes only.)
```

### Review

- Reviewers will leave comments inline. **Reply to every comment** even if it's just "fixed in `<sha>`".
- Push fixes as new commits — we squash on merge, so you don't need to clean up history yourself.
- Be patient — open-source review happens around day jobs.

### Merging

Once approved and CI is green, a maintainer will squash-merge your PR. You'll
be credited in the commit message and (eventually) in [`CHANGELOG.md`](CHANGELOG.md).

---

## Testing

All tests live in [`tests/`](tests/) and run with **pytest**:

| File | Coverage | Example |
|:---|:---|:---|
| `test_retrieval.py` | tokenizer, BM25, hybrid retriever, schema loader | `pytest tests/test_retrieval.py::TestBM25Index -v` |
| `test_agent.py` | SQL safety filter, model router, every node, end-to-end graph | `pytest tests/test_agent.py::TestGraphE2E -v` |
| `test_api.py` | FastAPI endpoints with `TestClient` | `pytest tests/test_api.py::TestPostQuery -v` |

### Writing new tests

- **Pure unit tests** — stub external services. The agent tests show how to
  monkey-patch nodes (`monkeypatch.setattr(graph_module, "classify_intent_node", fake)`)
  and how to fake `httpx.Client` for the eval runner.
- **API tests** — use FastAPI's `TestClient`. Fake `run_agent` and `get_cursor`
  rather than spinning up a real backend.
- **Retrieval tests** — feed the `HybridRetriever` a `_StubLoader` and a
  `_StubEmbedder`; do not depend on a real DB or embedding API.
- **Test naming** — `test_<what>_<expected_behavior>` (e.g.
  `test_retry_count_2_forces_strong`).
- **One assertion per test, where possible.** Multiple `assert` lines are
  fine; one `assert` per *concept* is the rule.

### Coverage goals

Phase 1 doesn't enforce a coverage percentage, but we do expect:

- Every public function in `src/agent/`, `src/retrieval/`, and `src/router/` has at least one test
- Every API endpoint in `src/api/` has at least one happy-path test and one error-path test
- Every safety branch in `src/db/executor.py` has a regression test

---

## Adding to the Eval Suite

The evaluation harness (`eval/`) is the Phase 1 acceptance gate. Adding cases
is one of the highest-leverage contributions you can make.

### When to add a case

- A user reported a query that produced a wrong answer
- A new prompt or retrieval change might regress an existing behavior
- A new table or column is added to the warehouse
- You found a category of question we don't currently cover

### How

Open [`eval/test_queries.yaml`](eval/test_queries.yaml) and add an entry:

```yaml
  - id: 15                                          # next free id
    category: aggregation                           # one of: simple_query | aggregation | multi_join | exploration | ranking
    query: "your natural-language question"
    expected_tables: [dwd_order_detail]             # any one of these in the SQL counts as a recall hit
    expected_sql_contains: ["sum", "group by"]      # case-insensitive substring matches
    expected_result_check:
      type: row_count                               # single_value | first_row | row_count | non_empty
      condition: "count >= 1"                       # python expression evaluated against `count` or `value`
```

Then run the eval to make sure your case is reasonable:

```bash
python eval/run_eval.py --filter <category>
```

If the existing pipeline can't yet pass your new case, that's fine — open the
PR anyway and label it as a regression test for an outstanding issue.

---

## Working with the Database

### Schema changes

If you change [`db/init.sql`](db/init.sql) you must also:

1. Update the matching seed in [`db/seed_data.sql`](db/seed_data.sql)
2. Update [`db/data_dictionary.yaml`](db/data_dictionary.yaml) with the new table/column descriptions
3. Re-run the embedder bootstrap so the retriever picks up the new entries:

   ```bash
   python -m src.retrieval.bootstrap
   ```

4. Add or update test cases in [`eval/test_queries.yaml`](eval/test_queries.yaml) so the new schema is exercised
5. Reset the dev DB volume and re-create it (fresh `init.sql` only runs on an empty data directory):

   ```bash
   docker compose down -v
   docker compose up -d
   ```

### Switching embedding models

pgvector columns are dim-typed, so going from a 1536-dim model to a 3072-dim
model needs a full table rebuild. The bootstrap script handles this — it
DROPs and re-CREATEs `schema_embeddings` with the new dim:

```bash
python -m src.retrieval.bootstrap
```

---

## Working with LLM Providers

Elytra is **OpenRouter-first**: one key routes every model. The router accepts
`vendor/model` names (e.g. `deepseek/deepseek-chat`,
`anthropic/claude-sonnet-4`). Bare names like `deepseek-chat` are
auto-prefixed via `_OPENROUTER_MODEL_ALIASES` in
[`src/agent/llm.py`](src/agent/llm.py).

If you only have per-vendor keys (`OPENAI_API_KEY` etc.), they'll be picked up
when `OPENROUTER_API_KEY` is empty — see `_resolve_client()` in `llm.py` for
the fallback order.

### Adding a new provider

1. Add an `_OPENROUTER_MODEL_ALIASES` entry mapping the bare name to its `vendor/model` form
2. (If the provider needs special API handling) add an adapter in `src/agent/llm.py`
3. Add a test in `tests/test_agent.py` covering the model router branch

---

## Community

- **Issues**: <https://github.com/shuheng-mo/Elytra/issues>
- **Discussions**: <https://github.com/shuheng-mo/Elytra/discussions> (for design questions and proposals)
- **Maintainer**: [@shuheng-mo](https://github.com/shuheng-mo)

When in doubt, open an Issue or a Draft PR — it's much easier to align early
than to argue late.

---

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).

---

<div align="center">

<img src="assets/elytra-logo-hex-icon.svg" width="48" alt="Elytra" />

**Thanks for contributing!** 🪶

**[⬆ Back to top](#contributing-to-elytra)**

</div>
