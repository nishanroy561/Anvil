# STATUS — implementation handoff

> **The current state of the build. Read this first, then `plan.md`, then the
> `plan/` doc for whatever you're implementing. Update this file whenever you
> complete meaningful work — it is the handoff between sessions and models.**

Last updated: 2026-07-20 (Phase 2 session)

## Where we are

**Phase 0 (scaffold & infra): DONE and verified.**
**Phase 1 (schema, models, auth): DONE — acceptance criteria pass.**
**Phase 2 (ingestion pipeline): DONE — acceptance criteria pass (94 tests green
+ the 1k-trace worker-kill load test).**
Phases 3–11: not started (stubs with spec pointers exist).

**Rule added this session:** frontend work (`apps/web`, components, styling)
requires telling the user what you intend to build and getting a go-ahead first.
Backend/API/worker/SDK work proceeds without asking. See `CLAUDE.md` — this
matters most at Phase 4.

### Verified working on this machine (Windows, Python 3.12, Node 20, Docker 28)

- `docker compose up -d postgres redis --wait` → both healthy (Redis runs with AOF).
- `pip install -e .[dev]` in `apps/api` → clean; `python -m pytest -q` → 2 smoke tests pass.
- `python -m alembic upgrade head` → migration `0001` applies cleanly (all 16 tables,
  pg_trgm, trigram GIN, partial unique feedback index, unique (run_id, item_id)).
- `python -m anvil_api.seed` → demo org (plan `enterprise`) / demo@anvil.dev / demo1234 /
  Demo Project / API key printed once.
- `uvicorn anvil_api.main:app` → `GET /healthz` = `{db: true, redis: true}`;
  login 200 with seeded creds, 401 with wrong password; session cookie
  authenticates `GET /v1/projects` (returns Demo Project). `/metrics` exposed.
- Manual curl acceptance over real HTTP (architecture §7): signup → cookie →
  `POST /v1/projects` → `POST .../api-keys` (full key once) → list returns
  prefixes only. Test data removed afterwards; dev DB holds only the seed.
- `cd apps/api && python -m pytest -q` → **69 passed** (~25 s). Must be run from
  `apps/api` — pytest needs `alembic.ini` on the rootdir.
- `cd apps/worker && python -m pytest -q` → **25 passed** (~7 s).
- **Phase-2 acceptance** (`scripts/loadtest_ingest.py`): 1,000 traces / 7.5k
  spans over real HTTP with the worker SIGKILLed at trace 350 and restarted →
  zero loss, zero duplicates, and list/detail/overview/search all correct.
  Requires the API running; see the script docstring.

### Phase 1 test suite (`apps/api/tests/`)

Integration tests build their own `anvil_test` database (dropped/recreated and
migrated by Alembic each session — this is the "clean migrate on a fresh DB"
check) and use Redis DB 15. Dev data is never touched. With Postgres/Redis
down they **skip** with a pointer to the compose command, so `pytest -q` on a
bare checkout stays green (2 smoke tests pass, 19 skip).

- `test_auth.py` — signup creates org+user+default project; duplicate email
  can't be enumerated; wrong-password and unknown-user 401s are byte-identical;
  tampered cookie rejected; logout clears; validation 422s.
- `test_tenancy.py` — full key returned once then prefixes only; valid key
  resolves its project; revoked key 401s; other tenants' project/key reads,
  writes and deletes all **404, never 403**.
- `test_rate_limit.py` — 11th login/min from one IP 429s while other IPs are
  fine; per-email limit (5) trips across rotating IPs; rejections exported to
  `/metrics`; limiter **fails open** when Redis raises.
- `test_seed.py` — seeds the enterprise-plan demo tenant, prints the key once,
  is idempotent, and the seeded creds log in.
- Schema spot-check on the migrated test DB: 16 tables + `alembic_version`,
  `pg_trgm` installed, `ix_traces_search_trgm` (trigram GIN) and
  `ix_traces_metadata_gin` present — checklist row 26's Phase-1 half.

### Phase 2 test suite

API (`apps/api/tests/`):
- `test_normalize.py` — no services needed. Oversized fields truncated with
  markers, clock skew/negative durations clamped, dangling/self/cyclic parents
  nulled (checklist rows 6–7). The rule under test: **repair and flag, never
  reject**.
- `test_ingest.py` — 202 + queued payload; `project_id` refused from the payload
  (the schema's `additionalProperties: false` makes it unexpressible) and always
  stamped from the key; 422 with per-trace error paths; 413; 400; revoked key
  401; backpressure 429 vs per-key 429 (distinct codes); traces/min billed per
  trace not per request; `/v1/ingest/stats`.
- `test_read_api.py` — filters, metadata containment, cursor pagination visiting
  every row exactly once, search fences (3-char floor, time bound), detail with
  spans, overview aggregates/buckets, cross-tenant 404s.
- `test_schema_sync.py` — vendored schema copy matches `packages/shared/`.

Worker (`apps/worker/tests/`):
- `test_consumer_guards.py` — round trip (also the ADR-0014 column-drift guard),
  redelivery idempotent, **tenant guard** blocks cross-tenant overwrite,
  **monotonic guard** keeps a completion over a late `in_progress` while still
  admitting real progress, in-batch duplicate merge, and a 500x8 batch that
  regression-tests the bind-parameter limit.
- `test_consumer_stream.py` — ack+delete, undecodable → DLQ, poison message
  dead-lettered after 5 deliveries without blocking healthy traffic, and pending
  entries from a dead consumer reclaimed and persisted.
- `test_reaper.py` — stale `in_progress` → `abandoned`, fresh ones untouched, and
  a late completion still overriding `abandoned`.
- `test_derive.py` — no services needed. Cost from the price table, unknown model
  → null (never an error), span-model fallback, roll-ups, latency, and
  `search_text` covering trace name + root payloads but **not** span payloads.

### Known quirks

- `alembic`/`uvicorn`/`pytest` CLIs are not on PATH on this machine (pip user
  install) — use `python -m alembic`, `python -m uvicorn`, `python -m pytest`.
- passlib 1.7.4 logs a harmless "error reading bcrypt version" with bcrypt≥4.1;
  pyproject pins `bcrypt<4.1`. If the warning appears, reinstall deps.
- `make` is not available on this Windows box; run the commands from CLAUDE.md
  directly. The Makefile is still authoritative for Linux/CI.
- pytest-asyncio runs on a **session-scoped event loop** (pinned in
  `pyproject.toml`). The app builds its SQLAlchemy engine and Redis pool at
  import time and those are loop-bound; per-test loops hand the second test a
  dead pool. Keep this in mind when adding async fixtures.
- Errors currently serialize as `{"detail": {"code", "message"}}` (FastAPI wraps
  `HTTPException.detail`), not the `{"error": {...}}` envelope in CLAUDE.md.
  Tests assert the *current* shape. Phase 11 sweep item 2 owns unifying this —
  when it lands, the `["detail"]["code"]` assertions move to `["error"]["code"]`.
- **Postgres caps a statement at 32767 bind parameters.** A full 500-trace batch
  carries several thousand spans and blew straight past it — the consumer chunks
  multi-row inserts (`_chunked`) inside one transaction. Only the load test
  caught this; small-batch unit tests never reach the limit. Any new multi-row
  insert needs the same treatment.
- After a worker kill, orphaned entries wait for `reclaim_idle_ms` (60 s) before
  a survivor takes them, so the load test's drain takes ~60 s. That is the
  designed trade-off, not a stall.
- The trigram index is confirmed usable (`EXPLAIN` shows a Bitmap Index Scan on
  `ix_traces_search_trgm`), but at ~1k rows the planner correctly prefers
  `ix_traces_project_started` and filters. Phase 11's 50k load test is where the
  trigram path gets verified under scale.

## What exists

- Root: compose (postgres/redis/api/worker/web + `observability` profile:
  prometheus :9090, grafana :3001, tempo, otel-collector), `.env.example`,
  Makefile, MIT LICENSE, CLAUDE.md.
- `infra/observability/`: prometheus scrape config + alert rules (per
  plan/observability.md, incl. the stuck-vs-dead worker distinction), grafana
  provisioning + ingestion dashboard JSON, tempo/otel configs. **Unverified**
  (profile never booted); verify when Phase 2 metrics exist.
- `packages/shared/trace-schema.json`: wire-format JSON Schema (draft 2020-12),
  the single source of truth. `scripts/sync_schemas.py` copies it into
  `apps/api/anvil_api/trace_schema.json` (service images build from their own
  directory and can't COPY `packages/`); the copy is a build artifact and
  `test_schema_sync.py` fails on drift.
- `scripts/loadtest_ingest.py`: the Phase-2 acceptance harness (see above).
- `apps/api`: Phases 1–2 — config, async SQLAlchemy models for **all 16 tables**,
  Alembic migration `0001` (the complete v1 schema — later phases should not
  need migrations), auth (bcrypt sessions, hashed API keys, generic errors),
  fixed-window fail-open rate limiter, project + API-key routers (tenant 404s),
  seed, **ingest endpoint** (`routers/ingest.py` + `services/ingest.py` +
  `services/normalize.py`), **read APIs** (`routers/traces.py`), `deps.py`
  (shared tenant scoping), `streams.py`, and backlog gauges refreshed from the
  lifespan task (ADR-0009 — the API owns queue depth, not the worker).
- `apps/worker`: **ingest consumer implemented** — `ingest_consumer.py`
  (consumer group, guarded upserts, reclaim, DLQ, reaper), `derive.py` (cost +
  `search_text`), `tables.py` (Core table declarations — ADR-0014), `registry.py`
  (pricing resolver registry with the cloud-mode built-ins-only rule),
  `config.py`, `db.py`, `metrics.py` (:9101). `eval_runner`, `sampler`,
  `alerter`, `retention` remain spec-docstring stubs.
  `pricing.py` has a starter price table — **prices are still unverified against
  live provider pricing pages**; check before any cost claim is user-facing.
- `apps/web`: minimal Next.js 14 App Router + Tailwind page (placeholder until
  Phase 4). `npm install` has NOT been run yet.
- `sdks/`, `examples/`: stubs with spec pointers only.

## Next actions (in order)

1. **Phase 3 — SDKs** (plan/sdk.md): Python + TS clients, LangChain callback
   handler, demo app. ADR-0013 governs: the SDK never blocks or crashes the host
   app. The wire format they emit is `packages/shared/trace-schema.json`, and
   `/v1/ingest/traces` now enforces it strictly (`additionalProperties: false`)
   — an SDK sending an extra field gets a 422, so build against the schema.
   Checklist rows 6 (client-side truncation) and 15 (`redact` hook) are Phase 3's.
2. Then Phase 4 (dashboard) — **stop and check with the user before starting UI
   work**, per the rule in `CLAUDE.md`. The read APIs it needs already exist and
   are tested; `scripts/loadtest_ingest.py` generates realistic data to build
   against (it provisions its own tenant and prints the project id).
3. Then Phase 5 onward per plan.md's phase table.

### Phase 2 leftovers worth knowing

- The **feedback stream** (`ingest:feedback`) is declared in both `streams.py`
  modules but has no consumer — that is Phase 9's work (evaluation.md §7), as
  scoped.
- `/v1/ingest/stats` is API-key authed (a customer diagnosing their own key).
  If the dashboard needs it, it needs a session-authed variant.
- `infra/observability/` is **still unverified** — the profile has never been
  booted. The metrics it scrapes now exist (API: ingest counters + backlog
  gauges; worker :9101: persist/lag/guard-block counters), so this can finally
  be checked; Phase 11 owns the dashboards/alerts acceptance.

## Environment note

Postgres/Redis containers may still be running from this session
(`docker compose ps`; data persists in volumes `anvil_pgdata`/`anvil_redisdata`).
The seeded API key from this session should be treated as dev-only.

The dev database was left clean: the seeded demo org only, no traces. Load-test
tenants and their 1k traces were deleted after each run, and the `ingest:traces`
/ `ingest:dlq` streams were cleared. Regenerate data any time with
`python scripts/loadtest_ingest.py --traces 1000` (drop `--kill-worker` for a
plain data load) while the API and a worker are running.
