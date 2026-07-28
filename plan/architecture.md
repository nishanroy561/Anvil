# Architecture — Foundations, Data Model, Auth (Phases 0–1)

> Part of the [Anvil implementation plan](../plan.md). This doc owns: system shape, repo layout, conventions, the complete database schema, auth, and the Phase 0/1 build steps. Other subsystems: [ingestion](ingestion.md) · [sdk](sdk.md) · [dashboard](dashboard.md) · [evaluation](evaluation.md) · [retention](retention.md) · [observability](observability.md).

## 1. System shape

```
customer app ──SDK──▶ FastAPI (api) ──XADD──▶ Redis Streams ──worker──▶ PostgreSQL
                                                                            │
        browser ◀── Next.js (web) ◀────────── FastAPI read APIs ◀───────────┘
```

Five processes, all composed via Docker: `api` (FastAPI/Uvicorn), `worker` (Python loops: ingest consumer, eval runner, sampler, alerter, retention), `web` (Next.js), `postgres:16`, `redis:7`. The write path is validate-and-enqueue (no DB writes on the request path); the read path skips Redis entirely. Workers are throughput-tuned, not latency-tuned — a trace appearing in the dashboard 1–2 s after the fact is fine.

**Stack (fixed — do not substitute):** FastAPI, PostgreSQL 16, Redis 7, Next.js 14+ (App Router), Tailwind, LangChain/LangGraph (judge orchestration), Pandas/NumPy (analysis), Docker. Self-observability: Prometheus, Grafana, OpenTelemetry ([observability.md](observability.md)).

## 2. Repository layout (created in Phase 0)

```
Anvil/
├── README.md
├── plan.md                     # slim execution plan (phase order + acceptance)
├── plan/                       # subsystem specs (this folder) + adr/ decision records
├── docker-compose.yml          # postgres, redis, api, worker, web (+ observability profile: prometheus, grafana, tempo, otel-collector)
├── .env.example                # all env vars, documented
├── Makefile                    # make up / api / web / worker / test / migrate / seed
├── infra/
│   └── observability/          # prometheus.yml + alert rules, grafana provisioning, tempo.yml, otel-collector.yml
├── apps/
│   ├── api/                    # FastAPI service (ingestion + query + eval APIs)
│   │   ├── pyproject.toml
│   │   ├── alembic/            # migrations
│   │   └── anvil_api/
│   │       ├── main.py
│   │       ├── config.py       # pydantic-settings, reads env
│   │       ├── db.py           # async SQLAlchemy engine/session
│   │       ├── redis_client.py
│   │       ├── auth.py         # API-key + session auth, tenant scoping
│   │       ├── models/         # SQLAlchemy models
│   │       ├── schemas/        # Pydantic request/response models
│   │       ├── routers/        # ingest, traces, datasets, evals, alerts, feedback, auth
│   │       └── services/       # business logic (no logic in routers); rate_limit.py, analysis.py
│   ├── worker/
│   │   └── anvil_worker/
│   │       ├── registry.py          # plugin registries: graders, alert channels, pricing (plugins.md)
│   │       ├── graders/             # built-in graders, registered via the plugin contract
│   │       ├── ingest_consumer.py   # Redis streams (traces + feedback) → Postgres batch writer
│   │       ├── eval_runner.py       # eval batch jobs
│   │       ├── sampler.py           # scheduled live-traffic scoring
│   │       ├── alerter.py           # regression detection + alert firing
│   │       └── retention.py         # plan-based retention sweeps
│   └── web/                    # Next.js dashboard
│       └── src/ (app/, components/, lib/)
├── sdks/
│   ├── python/                 # anvil-sdk (pip; also hosts the eval CLI)
│   └── typescript/             # @anvil/sdk (npm)
├── packages/
│   └── shared/                 # JSON Schemas for trace/span wire format (source of truth)
└── examples/
    ├── demo-rag-app/           # instrumented demo app; doubles as seed-data generator
    ├── ci-gate/                # example GitHub Action using the eval CLI
    └── plugin-example/         # installable plugin package: grader + alert channel (plugins.md)
```

## 3. Conventions (all phases)

- Python 3.11+, `pyproject.toml` (`uv` or pip); `ruff` format; `mypy` best-effort.
- All FastAPI endpoints async; DB via async SQLAlchemy 2.x; schema changes **only** via Alembic migrations after Phase 1.
- Every API route is tenant-scoped (§5). No query may touch another tenant's rows. 404 (never 403) for other tenants' resources.
- **UTC everywhere:** `timestamptz` stored UTC; crons, `date_trunc` bucketing, and alert windows evaluate in UTC. Timezone rendering is frontend-only.
- Tests as you go: `pytest` (API/worker, against compose Postgres/Redis or testcontainers), `vitest` (TS SDK). Mock all LLM calls in tests (LangChain fake LLMs / respx) — the suite runs keyless and free.
- Keep routers thin; business logic in `services/` and worker modules.
- Commit at least at the end of each phase (git init in Phase 0).
- Consistent error shape everywhere: `{"error": {"code", "message"}}`.

## 4. Data model (migrated in full in Phase 1)

Multi-tenancy: **organization → projects → everything else**. Every domain table carries `project_id`; auth resolves a credential to an org/project, and queries always filter by ownership. `project_id` always comes from the resolved credential, **never** from a request body.

```
organizations(id uuid pk, name, plan text default 'free',  -- vestigial, unread (ADR-0019)
  plan_overrides jsonb null,  -- vestigial, unread (ADR-0019)
  created_at)
users(id uuid pk, org_id fk, email unique, password_hash, created_at)
projects(id uuid pk, org_id fk, name, slug,
  alert_webhook_url text null,  -- alert notification channel (evaluation.md); available to everyone
  created_at)
api_keys(id uuid pk, project_id fk, key_hash, key_prefix, name, created_at, revoked_at)

traces(
  id uuid pk,                 -- client-generated (SDK), idempotency key
  project_id fk,
  name,                       -- e.g. "answer_support_question"
  status,                     -- ok | error | in_progress | abandoned
  input jsonb, output jsonb, metadata jsonb,
  search_text text null,      -- lowercased projection of name + input/output text, capped 4 KB;
                              -- built by the ingest worker, exists solely for trigram search
  model, total_tokens_in int, total_tokens_out int,
  total_cost_usd numeric(12,6), latency_ms int,
  session_id text null, user_ref text null,   -- customer's end-user id (opaque)
  started_at timestamptz, ended_at timestamptz, created_at
)
spans(
  id uuid pk, trace_id fk on delete cascade,  -- cascade: retention deletes traces, children follow
  parent_span_id uuid null,
  project_id fk,              -- denormalized for direct filtering
  name, type,                 -- llm | retrieval | tool | embedding | chain | custom
  input jsonb, output jsonb, attributes jsonb,  -- model, temperature, prompt_template, prompt_variables, retrieved_chunks, scores…
  tokens_in int null, tokens_out int null, cost_usd numeric(12,6) null,
  status, error text null,
  started_at timestamptz, ended_at timestamptz
)

datasets(id uuid pk, project_id fk, name, description, created_at)
dataset_items(
  id uuid pk, dataset_id fk,
  input jsonb, expected_output jsonb null, metadata jsonb,
  source_trace_id uuid null   -- set when promoted; ON DELETE SET NULL — the item copies
)                             -- input/output, so it must survive retention deleting its trace

eval_runs(
  id uuid pk, project_id fk, dataset_id fk,
  name, status,               -- queued | running | completed | failed | cancelled
  target_config jsonb,        -- prompt template / model / params being tested
  grader_config jsonb,        -- list of checks + judge configs
  item_ids jsonb null,        -- dataset item-ID snapshot frozen at run start (evaluation.md)
  progress int default 0, total int default 0,
  summary jsonb null,         -- aggregate scores, computed on completion
  created_at, started_at null, completed_at null,
  updated_at                  -- doubles as the runner heartbeat (crash recovery)
)
eval_results(
  id uuid pk, run_id fk, item_id fk,
  output jsonb, scores jsonb, judge_reasoning jsonb null,
  latency_ms int, cost_usd numeric(12,6), error text null, created_at
)

feedback(
  id uuid pk, project_id fk, trace_id fk on delete cascade,
  rating smallint,            -- -1 thumbs-down / +1 thumbs-up
  comment text null, user_ref text null, created_at
)
-- partial unique index (trace_id, user_ref) WHERE user_ref IS NOT NULL:
-- repeat feedback from the same end-user upserts (rating toggle), never accumulates rows

alert_rules(id uuid pk, project_id fk, name, metric, -- cost | latency_p95 | error_rate | eval_score
  comparison, threshold numeric, window_minutes int, enabled bool, created_at)
alert_events(id uuid pk, rule_id fk, fired_at, value numeric, resolved_at null, notified bool)

monitor_configs(id uuid pk, project_id fk, name, sample_rate numeric,
  grader_config jsonb, schedule_cron text, enabled bool, last_run_at null)
monitor_scores(id uuid pk, monitor_id fk, trace_id fk on delete cascade, scores jsonb, created_at)

org_usage(                    -- monthly rollups for the usage panel (retention.md §2)
  org_id fk, month date,      -- pk (org_id, month)
  traces_ingested bigint, eval_items bigint, storage_bytes bigint null, updated_at)
```

**Required indexes** (high write volume + dashboard queries):
- `traces(project_id, started_at desc)` — the main list query.
- `spans(trace_id)`, `spans(project_id, started_at)`.
- `traces(project_id, status)`, `feedback(trace_id)`, `eval_results(run_id)`, `eval_results(run_id, item_id)` **unique** (per-item idempotency for eval-run resume).
- GIN on `traces.metadata` (jsonb_path_ops). Do **not** GIN-index span payloads (write amplification); span content is fetched by trace_id, never searched.
- GIN `traces.search_text gin_trgm_ops` (`pg_trgm` extension) — makes free-text `ILIKE '%q%'` an index scan. A bare ILIKE over JSONB seq-scans and dies around a few hundred thousand traces; the 4 KB cap on `search_text` bounds index size and per-insert cost. Query rules live in [ingestion.md](ingestion.md).

**Wire format:** defined once in `packages/shared/trace-schema.json`; both SDKs and the API validate against it. A batch is `{"traces": [{trace fields, "spans": […]}]}`. Trace/span IDs are client-generated UUIDs so ingestion is idempotent (guarded upserts — see [ingestion.md](ingestion.md)).

## 5. Auth model

Two credential types:
1. **SDK API keys** (`ak_<prefix>_<secret>`), per project. Store only SHA-256 hash + display prefix. Used by `/v1/ingest/*` and `/v1/feedback`. Resolved key → project_id; ingested data is force-stamped with that project_id regardless of payload contents.
2. **Dashboard sessions**: email+password (bcrypt via passlib) → signed HTTP-only session cookie. Users belong to one org; dashboard queries join through `projects.org_id = current_user.org_id`. No OAuth/SSO/roles in v1.

**Rate-limiter utility** (built in Phase 1, reused by every subsystem): `services/rate_limit.py` — fixed-window Redis counter (`INCR` + `EXPIRE`, key `rl:{scope}:{id}:{window}`), exposed as FastAPI dependency `rate_limit(scope, limit, window_s)`. On breach: `429` with `Retry-After` + `X-RateLimit-Remaining`. **Fail-open if Redis is down** (don't take auth down with the buffer). Fixed-window allows ~2× burst at window edges — acceptable at these generous limits.

Applied from day one to auth: login/signup per-IP (10/min) **and** per-email (5/min) against credential stuffing; generic error on breach (no user enumeration). Other surfaces: ingest ([ingestion.md](ingestion.md)), feedback + eval spend guards ([evaluation.md](evaluation.md)), dashboard reads (Phase 11 backstop, ~240/min/session).

## 6. Phase 0 — Scaffold & Infrastructure (build steps)

1. `git init`; `.gitignore` (Python, Node, `.env`, `.next`, `__pycache__`, dist).
2. Create the repo layout above (minimal boilerplate).
3. `docker-compose.yml`: `postgres:16` (volume, healthcheck); `redis:7` with **`--appendonly yes` + volume** (buffered-but-unpersisted traces must survive a Redis restart, or ingestion's zero-loss guarantee is void); `api`, `worker`, `web` (build from apps/, depends_on healthy backing services). Ports: 5432, 6379, 8000 (api), 3000 (web). Add an **`observability` compose profile** — `prometheus` (:9090), `grafana` (**:3001**, 3000 is taken by web; provisioned, anonymous viewer for demos), `otel-collector` (:4317/:4318), `tempo` — see [observability.md](observability.md). Support running api/web natively against dockerized postgres/redis for dev; document both.
4. `.env.example`: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` (judges — [ADR-0016](adr/0016-model-name-picks-the-provider.md)), `WEB_API_BASE_URL`, `DEPLOYMENT_MODE=self_hosted` (gates third-party plugin loading on shared deployments — [ADR-0019](adr/0019-fully-open-source-no-commercial-tiers.md)). Add an MIT `LICENSE` file in this step too. The file grows with later phases (rate limits, `RETENTION_DAYS`, `OTEL_*`) — every new env var lands here, documented, in the phase that introduces it.
5. `apps/api`: FastAPI with `/healthz` (checks DB + Redis), CORS for web origin, structured JSON logging.
6. `apps/web`: `create-next-app` (TS, Tailwind, App Router, src dir); one page rendering "Anvil".
7. `Makefile`: `make up / api / web / worker / test / migrate / seed`.

**Acceptance:** `docker compose up` → postgres+redis healthy; `GET :8000/healthz` returns `{"db": true, "redis": true}`; `:3000` renders. Committed.

## 7. Phase 1 — Schema, Models, Auth (build steps)

1. Alembic (async). Migration(s) creating **all** tables and indexes from §4 up front, including `CREATE EXTENSION IF NOT EXISTS pg_trgm` before the trigram index. Later phases touch code, not schema.
2. SQLAlchemy models mirroring §4; shared `Base`, uuid4 PK defaults, `created_at` server defaults.
3. Auth per §5: signup (creates org + first user), login, logout, `me`; the rate-limiter utility; `current_user()` and `current_project_from_api_key()` dependencies (Bearer `ak_…`, hash, reject revoked).
4. Project & key management: `POST/GET /v1/projects`, `POST /v1/projects/{id}/api-keys` (full key returned **once**), `DELETE /v1/api-keys/{id}` (revoke), `GET /v1/projects/{id}/api-keys` (prefixes only).
5. Seed (`make seed`): demo org (the `plan` column is vestigial — ADR-0019 — so its value no longer matters), user `demo@anvil.dev` / `demo1234`, project, API key printed once.
6. Tests: signup/login; cross-tenant access 404s; revoked key rejected; 11th login/min from one IP 429s while other IPs are fine; limiter fails open with Redis stopped.

**Acceptance:** `alembic upgrade head` clean on fresh DB; seed works; tests pass; manual curl signup → project → key succeeds.

## 8. Decisions & scope

**Rationale lives in the ADRs** — [`plan/adr/`](adr/README.md) records every load-bearing decision (why Redis Streams, why trigram over FTS/OpenSearch, why fully open source, why ClickHouse-later, plugin policy, …). Do not relitigate an Accepted ADR in review or implementation; changing one means writing a superseding ADR. New significant decisions made during the build get an ADR *when decided, not later*.

**Out of scope for v1 (do not build):** anything commercial — plans, quotas, metering for billing, payment processing ([ADR-0019](adr/0019-fully-open-source-no-commercial-tiers.md)); table partitioning for retention (ADR-0011); project/org deletion cascades; GDPR delete-by-user_ref; OAuth/SSO/roles; browser-side TS SDK; any second datastore for search/analytics (ADR-0005/0006); cloud-side third-party plugins (ADR-0012). If any becomes unavoidable, write the ADR recording the deviation.
