# STATUS — implementation handoff

> **The current state of the build. Read this first, then `plan.md`, then the
> `plan/` doc for whatever you're implementing. Update this file whenever you
> complete meaningful work — it is the handoff between sessions and models.**

Last updated: 2026-07-19 (initial base implementation session)

## Where we are

**Phase 0 (scaffold & infra): DONE and verified.**
**Phase 1 (schema, models, auth): code DONE and manually verified; automated acceptance tests NOT yet written.**
Phases 2–11: not started (stubs with spec pointers exist).

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

### Known quirks

- `alembic`/`uvicorn`/`pytest` CLIs are not on PATH on this machine (pip user
  install) — use `python -m alembic`, `python -m uvicorn`, `python -m pytest`.
- passlib 1.7.4 logs a harmless "error reading bcrypt version" with bcrypt≥4.1;
  pyproject pins `bcrypt<4.1`. If the warning appears, reinstall deps.
- `make` is not available on this Windows box; run the commands from CLAUDE.md
  directly. The Makefile is still authoritative for Linux/CI.

## What exists

- Root: compose (postgres/redis/api/worker/web + `observability` profile:
  prometheus :9090, grafana :3001, tempo, otel-collector), `.env.example`,
  Makefile, MIT LICENSE, CLAUDE.md.
- `infra/observability/`: prometheus scrape config + alert rules (per
  plan/observability.md, incl. the stuck-vs-dead worker distinction), grafana
  provisioning + ingestion dashboard JSON, tempo/otel configs. **Unverified**
  (profile never booted); verify when Phase 2 metrics exist.
- `packages/shared/trace-schema.json`: wire-format JSON Schema (draft 2020-12).
- `apps/api`: full Phase-1 implementation — config (incl. `DEPLOYMENT_MODE`,
  rate-limit/quota settings), async SQLAlchemy models for **all 16 tables**,
  Alembic migration `0001` (the complete v1 schema — later phases should not
  need migrations), auth (bcrypt sessions, hashed API keys, generic errors),
  fixed-window fail-open rate limiter applied to auth (per-IP 10/min,
  per-email 5/min), project + API-key routers (tenant 404s), seed, Prometheus
  instrumentation, smoke tests.
- `apps/worker`: runnable skeleton (idle heartbeat so compose is healthy);
  every future module (`ingest_consumer`, `eval_runner`, `sampler`, `alerter`,
  `retention`, `registry`) is a stub whose docstring is a condensed spec with
  plan/ADR pointers. `pricing.py` has a real starter price table (**verify
  prices against providers when Phase 2 lands**).
- `apps/web`: minimal Next.js 14 App Router + Tailwind page (placeholder until
  Phase 4). `npm install` has NOT been run yet.
- `sdks/`, `examples/`: stubs with spec pointers only.

## Next actions (in order)

1. **Finish Phase 1 acceptance** (plan/architecture.md §7 step 6): write the
   real tests — signup/login flow, cross-tenant 404, revoked key rejected,
   11th-login-per-minute 429 (other IPs unaffected), limiter fail-open with
   Redis stopped. Test infra suggestion: run against compose postgres/redis
   with a dedicated test database, or testcontainers.
2. **Phase 2** (plan/ingestion.md) — the ingest endpoint, stream consumer with
   guarded upserts, DLQ, reaper, derived fields, read APIs. The checklist rows
   1–7, 16, 26 in plan.md must all be covered by its tests.
3. Then Phase 3 onward per plan.md's phase table.

## Environment note

Postgres/Redis containers may still be running from this session
(`docker compose ps`; data persists in volumes `anvil_pgdata`/`anvil_redisdata`).
The seeded API key from this session should be treated as dev-only.
