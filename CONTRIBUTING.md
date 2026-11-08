# Contributing to Anvil

Thank you for considering a contribution. This document is written from a detailed
read of the codebase — the conventions and invariants here are things the code already
enforces, not aspirations. Read this alongside
[`plan/`](plan/) and [`PLUGINS.md`](PLUGINS.md) before opening a PR.

---

## Table of Contents

1. [Repository layout](#1-repository-layout)
2. [Getting a dev environment running](#2-getting-a-dev-environment-running)
3. [Codebase conventions](#3-codebase-conventions)
4. [Testing rules](#4-testing-rules)
5. [Adding a new API endpoint](#5-adding-a-new-api-endpoint)
6. [Adding a database column or table](#6-adding-a-database-column-or-table)
7. [Writing a grader or plugin](#7-writing-a-grader-or-plugin)
8. [Worker consumer changes](#8-worker-consumer-changes)
9. [Frontend changes](#9-frontend-changes)
10. [SDK changes](#10-sdk-changes)
11. [Invariants you must not break](#11-invariants-you-must-not-break)
12. [Pull request checklist](#12-pull-request-checklist)

---

## 1. Repository layout

```
apps/api/       FastAPI: ingest, read, auth, evals, monitors, feedback, usage
apps/worker/    Background consumers: persist, evals, monitors, alerts, retention
apps/web/       Next.js 14 dashboard
sdks/python/    anvil-sdk + anvil CLI (anvil eval run)
sdks/typescript/ @anvil/sdk
packages/shared/ Canonical wire-format JSON schema (synced into API and worker)
examples/       demo-rag-app · CI gate · plugin-example
plan/           Specs (ingestion.md, evaluation.md, …) and 23 ADRs
scripts/        Load-test and acceptance scripts
infra/          Prometheus / Grafana / Tempo / OTel Collector configs
```

`plan/` is not decoration. Before touching any non-trivial piece of logic, read the
relevant spec file — it records the reasons behind design choices that look wrong until
you know why they exist.

---

## 2. Getting a dev environment running

**Prerequisites:** Python ≥ 3.11, Node ≥ 20, Docker with Compose v2.

```bash
# 1. Start only the backing services (Postgres + Redis)
make up-svc           # or: docker compose up -d postgres redis --wait

# 2. Install each Python package in editable mode
cd apps/api    && pip install -e ".[dev]"
cd apps/worker && pip install -e ".[dev]"
cd sdks/python && pip install -e ".[dev]"

# 3. Install web dependencies
cd apps/web && npm install

# 4. Run migrations and seed demo data
make migrate   # alembic upgrade head
make seed      # prints an API key

# 5. Start processes in separate terminals
make api       # :8000 — uvicorn with --reload
make worker    # background consumers
make web       # :3000 — next dev
```

All tests build their own `anvil_test` database and use Redis DB 15. **Dev data is
never touched.** With services down, tests skip with a pointer rather than failing.

### Observability stack (optional)

```bash
make up-obs    # Prometheus :9090 · Grafana :3001 · Tempo · OTel Collector
```

---

## 3. Codebase conventions

### Python

- **Line length:** 100 characters (`ruff`, configured in each `pyproject.toml`).
- **Linter:** `ruff check` — run `make lint` before committing; it covers all Python
  trees (apps, SDKs, examples, scripts).
- **Type checker:** `mypy` is a dev dependency. Type annotations are expected on all
  new public functions.
- **Async throughout:** the API and worker are fully async. Do not call blocking I/O
  on the event loop. If a library has no async API, run it in a thread pool.
- **No cross-package imports:** `apps/api` must not import from `apps/worker` and vice
  versa (ADR-0014). Shared contracts live in `packages/shared/` and are vendored as
  JSON files synced by `scripts/sync_schemas.py`.
- **`get_settings()` is `@lru_cache`:** never mutate the returned object; use
  `monkeypatch.setenv` / re-importing in tests if you need different values.

### Error responses

Every failure the API produces must go through the error envelope defined in
[`apps/api/anvil_api/errors.py`](apps/api/anvil_api/errors.py):

```python
raise HTTPException(404, detail={"code": "not_found", "message": "Dataset not found"})
```

Do not return a bare string or a raw FastAPI 422. The exception handlers in `errors.py`
reshape every `HTTPException` and `RequestValidationError` into:

```json
{"error": {"code": "...", "message": "..."}}
```

Extra diagnostic keys (like `errors` on a batch ingest failure) go **inside** `detail`
alongside `code` and `message` — the envelope carries them through unchanged.

### Rate limiting

All rate limits are fixed-window counters in Redis
([`apps/api/anvil_api/services/rate_limit.py`](apps/api/anvil_api/services/rate_limit.py)).
New surfaces that are reachable without authentication or that could drive cost on
another tenant's behalf need a rate limit. Configure it in `config.py`; document it in
`.env.example` with a human-readable description.

### Logging

Use `logging.getLogger(__name__)` — never `print`. Every module that does meaningful
work has its own logger. Log levels: `DEBUG` for per-item trace detail, `INFO` for
lifecycle events, `WARNING` for recoverable problems, `ERROR` for failures that lose
data or degrade a user-facing feature.

---

## 4. Testing rules

### Run the suite

```bash
make test          # all suites: API, worker, Python SDK, TypeScript SDK, demo app
make test-api      # just the FastAPI suite
make test-worker   # just the worker suite
make lint          # ruff over every Python tree
make typecheck     # tsc --noEmit over the web app
```

### How tests are structured

API tests use `httpx.AsyncClient` against the live ASGI app — no mocking of the HTTP
layer. The `conftest.py` fixtures:

- `database` (session-scoped): drops and recreates `anvil_test`, runs Alembic.
- `clean_state` (per-test, autouse): `TRUNCATE` all tables + `FLUSHDB` on Redis DB 15.
- `tenant` / `tenant_factory`: creates a full isolated tenant (org → user → project →
  API key) with a fake peer IP so the per-IP auth rate limit doesn't fire between
  tenants in the same test.
- `client` / `client_for_ip`: `AsyncClient` variants for authed/IP-faked requests.

All LLM calls are mocked throughout — **the full suite runs without any API key.**

### What every new feature needs

1. **A happy-path test** covering the normal case end-to-end.
2. **A tenancy test**: a second tenant cannot read or modify the first tenant's
   resource. See `test_tenancy.py` for the pattern.
3. **An error-shape test**: responses carry `{"error": {"code": ..., "message": ...}}`.
   See `test_error_envelope.py`.
4. **A rate-limit test** if the endpoint has one (see `test_rate_limit.py`).

Tests that require services are decorated with `@requires_services` (from `conftest.py`)
so they skip cleanly when Postgres/Redis are not reachable.

---

## 5. Adding a new API endpoint

1. Add the route to the appropriate router under
   [`apps/api/anvil_api/routers/`](apps/api/anvil_api/routers/) — one file per domain.
2. Add request/response schemas under
   [`apps/api/anvil_api/schemas/`](apps/api/anvil_api/schemas/).
3. Register the router in
   [`apps/api/anvil_api/main.py`](apps/api/anvil_api/main.py) if it is a new file.
4. If the endpoint writes to Postgres, use `get_session` from `db.py` as a dependency
   and commit inside the route or service; never leave an uncommitted session.
5. If the endpoint writes to Redis, obtain the client via `get_redis()` and handle
   `RedisError` — the queue being unavailable must return 503, not 500.
6. Authorization: project-scoped endpoints resolve ownership through the caller's org
   (`current_caller` or `current_api_key_context`). There is no shortcut that skips the
   tenant check; if you find yourself writing a query without a `project_id` filter,
   stop and re-read the tenancy model.

---

## 6. Adding a database column or table

1. **Add the model** in the appropriate file under
   [`apps/api/anvil_api/models/`](apps/api/anvil_api/models/):
   - `core.py` — org, user, project, API key, usage
   - `tracing.py` — trace, span, feedback
   - `evaluation.py` — dataset, dataset item, eval run, eval result
   - `monitoring.py` — monitor, monitor score
2. **Generate a migration:**
   ```bash
   cd apps/api
   python -m alembic revision --autogenerate -m "describe the change"
   ```
3. **Review the generated migration** — autogenerate misses things (column ordering,
   server defaults, index types). Read the diff before committing it.
4. **Verify** `alembic upgrade head` on a clean database: `make clean && make up-svc && make migrate`.
5. **Update the worker's `tables.py`** if the worker queries the new table directly via
   Core expressions rather than the ORM.

### Index conventions

- All primary keys are `UUID`, client-generated (traces use client IDs as idempotency
  keys).
- Foreign keys that will be filtered on heavily carry a `btree` index.
- `traces.search_text` uses a GIN trigram index — add a similar index if you add a
  free-text searchable column.
- Keyset pagination fields (usually `created_at` + `id`) carry a composite index.

---

## 7. Writing a grader or plugin

See [`PLUGINS.md`](PLUGINS.md) for the full contract. The short version:

- Implement the `Grader` protocol (duck-typed, no import from `anvil_worker`).
- Register via Python entry point `anvil.graders`.
- Scores are always `0.0–1.0`.
- Populate `tokens_in` / `tokens_out` on LLM-backed graders so run cost is accurate.
- Set `compares_expected = True` if you score against `context.expected_output` — this
  enables Anvil's tautology detection (ADR-0018).
- Do not swallow errors silently. Raise — Anvil catches it, records an item-level
  grader error, and keeps the run going. A silent `1.0` is worse than an error.
- Built-in graders live in
  [`apps/worker/anvil_worker/graders/`](apps/worker/anvil_worker/graders/). Adding a
  new built-in means adding an entry to
  [`graders_catalog.json`](apps/worker/anvil_worker/graders_catalog.json) and syncing
  it into the API copy via `scripts/sync_schemas.py`.

---

## 8. Worker consumer changes

The worker runs as a set of concurrent `asyncio` tasks (see
[`apps/worker/anvil_worker/main.py`](apps/worker/anvil_worker/main.py)). Each task
class follows the same pattern:

```python
class MyConsumer:
    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        while self._running:
            await self._tick()
            await asyncio.sleep(POLL_S)
```

`stop()` is called by the SIGTERM handler so in-flight work finishes and is acked
before the process exits. **Do not skip the `stop()` method** — a consumer that
ignores the signal leaves pending messages for the reclaim path, which is noisier than
a clean shutdown.

### Delivery and idempotency

All stream consumers use consumer groups with explicit `XACK`. A message that errors
without ack is redelivered on the next `xautoclaim`. After 5 deliveries, it goes to
the dead-letter queue. Write every handler so that re-delivering the same message is
harmless — usually by using `INSERT ... ON CONFLICT DO UPDATE` (guarded upsert).

### Database access in the worker

Use the `transaction()` async context manager from
[`apps/worker/anvil_worker/db.py`](apps/worker/anvil_worker/db.py) — it gives you a
`Connection` and commits on clean exit, rolls back on exception. The worker uses SQLAlchemy
Core (not ORM) for its write path; table definitions live in
[`apps/worker/anvil_worker/tables.py`](apps/worker/anvil_worker/tables.py).

---

## 9. Frontend changes

The web app is Next.js 14 (App Router) + TypeScript + Tailwind CSS.

```bash
cd apps/web
npm install
npm run dev       # hot-reload dev server on :3000
npx tsc --noEmit  # type check (also: make typecheck)
npm run build     # verify production build
```

All API calls go through the same-origin `/api` proxy configured in
[`next.config.mjs`](apps/web/next.config.mjs). **Do not hard-code `localhost:8000`**
anywhere in the frontend — the proxy target is baked in at build time as
`WEB_API_BASE_URL`, and changing it requires a rebuild.

Key component locations:

| What | File |
|------|------|
| Trace waterfall | `src/components/Waterfall.tsx` |
| Span detail panel | `src/components/SpanDetail.tsx` |
| Eval run comparison UI | `src/components/evals/` |
| Sidebar navigation | `src/components/Sidebar.tsx` |
| Auth middleware | `src/middleware.ts` |

Components use Tailwind utility classes directly. Keep components focused: if a
component needs its own data-fetching, it should be a Server Component or use SWR.

---

## 10. SDK changes

Both SDKs share the same prime directive: **the SDK never blocks or crashes the host
app** (ADR-0013). This is not a comment — it is a load-bearing invariant enforced by
the test suite and tested by killing Anvil mid-request.

### Python SDK (`sdks/python/`)

- `_client.py` — `AnvilClient`: bounded `queue.Queue`, background flush thread,
  drop-on-overflow with counter.
- `_tracer.py` — `contextvars`-based trace/span context managers + `@observe` decorator.
- `_wire.py` — `Trace` / `Span` dataclasses; field truncation at `MAX_FIELD_BYTES`.
- `langchain.py` — `AnvilCallbackHandler` for LangChain chains.
- `cli.py` — `anvil eval run` CI gate; exit codes are the contract for CI workflows.

Rules:
- All exceptions in the SDK **must** be caught before they reach the caller. Use
  `except Exception` at the queue boundary and `except Exception` again in the flush
  thread. The `init()` function itself wraps in try/except.
- A `redact` hook that raises causes the object to be **dropped, never shipped
  un-scrubbed** — verify this if you touch the flush path.
- Wire format changes require updating `packages/shared/trace-schema.json` and running
  `scripts/sync_schemas.py` to propagate to the API and worker.

### TypeScript SDK (`sdks/typescript/`)

Same architecture: `client.ts` owns the queue and flush loop; `tracer.ts` uses
`AsyncLocalStorage` for span context; `wire.ts` is the serialization layer.

```bash
cd sdks/typescript
npm install
npm test
npm run build
```

---

## 11. Invariants you must not break

These are things the test suite explicitly verifies. A PR that breaks any of them will
not land.

| Invariant | Where it lives | Why |
|-----------|---------------|-----|
| Wrong password and unknown email return byte-identical 401 bodies | `test_auth.py` | No user enumeration |
| Every error response has shape `{"error": {"code", "message"}}` | `test_error_envelope.py` | Consistent client parsing |
| A tenant cannot read or modify another tenant's data | `test_tenancy.py` | Multi-tenancy |
| Ingest never writes to Postgres on the request path | `test_ingest.py` + architecture | Burst absorption |
| Dataset items copy their payloads; `source_trace_id` is SET NULL on trace delete | `test_datasets.py` | Datasets outlive retention |
| `item_ids` in an eval run is a snapshot frozen at start | `eval_runner.py` | Reproducible comparison |
| `updated_at` on `eval_runs` is the only heartbeat/crash-recovery signal | `eval_runner.py` | No separate lease table |
| Usage rollups are monotonic (`GREATEST`) | `retention.py` | Survive retention recompute |
| Comparison returns `inconclusive` for n < 30 or CI straddling zero | `test_analysis.py` | No false confidence |
| SDK swallows all exceptions before the caller sees them | `sdks/python/tests/` | Prime directive |
| A grader error is item-level, never run-level | `graders/__init__.py` | Silent pass prevention |

---

## 12. Pull request checklist

- [ ] `make lint` passes (ruff over every Python tree)
- [ ] `make typecheck` passes (tsc --noEmit)
- [ ] `make test` passes (all suites, with services running)
- [ ] New endpoint: tenancy test added
- [ ] New endpoint: error-shape test added
- [ ] New endpoint with rate limit: rate-limit test added
- [ ] New database column: Alembic migration included and reviewed
- [ ] New database column: `worker/tables.py` updated if worker queries it
- [ ] Wire format change: `packages/shared/trace-schema.json` updated + `sync_schemas.py` run
- [ ] New grader (built-in): entry in `graders_catalog.json` + API copy synced
- [ ] ADR written or cited if the change makes a load-bearing design decision
- [ ] `.env.example` updated if a new config key is introduced

---

## Where to start

Good first areas if you are reading the code for the first time:

1. **`apps/api/anvil_api/errors.py`** — The error envelope. Small, self-contained, and
   explains the philosophy of the whole API surface.
2. **`apps/api/anvil_api/routers/ingest.py`** — The ingestion path. Shows the layering
   of rate limiting → validation → backpressure in order.
3. **`apps/worker/anvil_worker/ingest_consumer.py`** — The persistence path. The
   guarded-upsert pattern, reclaim, and DLQ are all here.
4. **`apps/api/anvil_api/services/analysis.py`** — The comparison logic. Pure functions,
   well-commented, shows the statistical conservatism baked in by ADR-0008.
5. **`apps/api/tests/conftest.py`** — The test fixtures. Understanding this makes
   reading any test file straightforward.
