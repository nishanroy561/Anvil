# Anvil — Implementation Plan

> **Purpose of this document:** A step-by-step build guide for Anvil, an LLM observability + evaluation platform (see `README.md`). It is written to be executed by an AI coding agent (or human) phase by phase. **Do not skip phases** — each phase builds on the previous one and ends with verifiable acceptance criteria. Complete and verify a phase before starting the next.

---

## 1. What We Are Building (Summary)

A multi-tenant web platform where engineering teams monitor, test, and debug LLM features in their products:

1. **Tracing** — SDKs (Python + TypeScript) instrument customer apps and stream structured traces (full execution tree of one AI request: input, retrieval, prompt, model call, output, tokens, latency, cost) to a FastAPI ingestion service → Redis buffer → PostgreSQL (JSONB spans) → Next.js waterfall dashboard.
2. **Evaluation** — Promote real traces into datasets; run prompt/model variants against datasets; score with deterministic checks + LLM-as-judge; compare runs side-by-side with statistically meaningful diffs; gate prompt changes in CI.
3. **Continuous quality** — Scheduled sampling of live traffic, continuous scoring, drift/regression alerts on cost/latency/eval score.
4. **Feedback loop** — End-user thumbs-down attaches to its trace and can flow into eval datasets.

**Stack (fixed — do not substitute):** FastAPI, PostgreSQL 16, Redis 7, Next.js 14+ (App Router), Tailwind CSS, LangChain/LangGraph (judge orchestration), Pandas/NumPy (analysis), Docker + docker-compose (deployment).

---

## 2. Repository Layout (create in Phase 0)

```
Anvil/
├── README.md
├── plan.md                     # this file
├── docker-compose.yml          # postgres, redis, api, worker, web
├── .env.example                # all env vars, documented
├── Makefile                    # dev shortcuts (make dev, make test, make seed)
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
│   │       └── services/       # business logic (no logic in routers)
│   ├── worker/                 # Python background workers
│   │   └── anvil_worker/
│   │       ├── ingest_consumer.py   # Redis stream → Postgres batch writer
│   │       ├── eval_runner.py       # eval batch jobs
│   │       ├── sampler.py           # scheduled live-traffic scoring
│   │       └── alerter.py           # regression detection + alert firing
│   └── web/                    # Next.js dashboard
│       ├── package.json
│       └── src/
│           ├── app/            # App Router pages
│           ├── components/
│           └── lib/            # API client, types
├── sdks/
│   ├── python/                 # anvil-sdk (pip package)
│   │   └── anvil_sdk/
│   └── typescript/             # @anvil/sdk (npm package)
│       └── src/
├── packages/
│   └── shared/                 # JSON Schemas for trace/span payloads (source of truth)
└── examples/
    ├── demo-rag-app/           # small Python RAG app instrumented with the SDK (for demos/tests)
    └── ci-gate/                # example GitHub Action using the eval CLI
```

**Conventions for all phases:**
- Python 3.11+, `uv` or `pip` with `pyproject.toml`; format with `ruff`; type-check with `mypy` (best effort).
- All FastAPI endpoints async; all DB access via async SQLAlchemy 2.x; migrations via Alembic — **never** mutate schema outside a migration after Phase 1.
- Every API route is tenant-scoped (see §4 Auth). No query may touch another tenant's rows.
- **UTC everywhere:** all timestamps are `timestamptz` stored in UTC; cron schedules, metric bucketing (`date_trunc`), and alert windows are evaluated in UTC. Timezone rendering is a frontend concern only.
- Write tests as you go: `pytest` for API/worker (use `testcontainers` or the compose Postgres/Redis), `vitest` for the TS SDK. Each phase's acceptance criteria include its tests passing.
- Commit at the end of each phase at minimum (init git in Phase 0).

---

## 3. Core Data Model (implemented in Phase 1)

Multi-tenancy: **organization → projects → everything else**. Every domain table carries `project_id`; auth resolves an API key or session to an org, and queries always filter by project ownership.

```
organizations(id uuid pk, name, created_at)
users(id uuid pk, org_id fk, email unique, password_hash, created_at)
projects(id uuid pk, org_id fk, name, slug, created_at)
api_keys(id uuid pk, project_id fk, key_hash, key_prefix, name, created_at, revoked_at)

traces(
  id uuid pk,                 -- client-generated (SDK), idempotency key
  project_id fk, 
  name,                       -- e.g. "answer_support_question"
  status,                     -- ok | error | in_progress | abandoned
  input jsonb, output jsonb, metadata jsonb,
  model, total_tokens_in int, total_tokens_out int,
  total_cost_usd numeric(12,6), latency_ms int,
  session_id text null, user_ref text null,   -- customer's end-user id (opaque)
  started_at timestamptz, ended_at timestamptz, created_at
)
spans(
  id uuid pk, trace_id fk, parent_span_id uuid null,
  project_id fk,              -- denormalized for direct filtering
  name, type,                 -- type: llm | retrieval | tool | embedding | chain | custom
  input jsonb, output jsonb, attributes jsonb,  -- attributes: model, temperature, prompt_template, prompt_variables, retrieved_chunks, scores…
  tokens_in int null, tokens_out int null, cost_usd numeric(12,6) null,
  status, error text null,
  started_at timestamptz, ended_at timestamptz
)

datasets(id uuid pk, project_id fk, name, description, created_at)
dataset_items(
  id uuid pk, dataset_id fk,
  input jsonb, expected_output jsonb null, metadata jsonb,
  source_trace_id uuid null   -- set when promoted from a trace
)

eval_runs(
  id uuid pk, project_id fk, dataset_id fk,
  name, status,               -- queued | running | completed | failed | cancelled
  target_config jsonb,        -- prompt template / model / params being tested
  grader_config jsonb,        -- list of checks + judge configs
  progress int default 0, total int default 0,
  summary jsonb null,         -- aggregate scores, computed on completion
  created_at, started_at null, completed_at null
)
eval_results(
  id uuid pk, run_id fk, item_id fk,
  output jsonb,               -- what the target produced for this item
  scores jsonb,               -- {"valid_json": 1, "accuracy": 0.8, "hallucination": 0, …}
  judge_reasoning jsonb null, latency_ms int, cost_usd numeric(12,6),
  error text null, created_at
)

feedback(
  id uuid pk, project_id fk, trace_id fk,
  rating smallint,            -- -1 thumbs-down / +1 thumbs-up
  comment text null, user_ref text null, created_at
)
-- partial unique index (trace_id, user_ref) WHERE user_ref IS NOT NULL:
-- repeat feedback from the same end-user upserts (rating toggle) instead of accumulating rows

alert_rules(id uuid pk, project_id fk, name, metric, -- cost | latency_p95 | error_rate | eval_score
  comparison, threshold numeric, window_minutes int, enabled bool, created_at)
alert_events(id uuid pk, rule_id fk, fired_at, value numeric, resolved_at null, notified bool)

monitor_configs(id uuid pk, project_id fk, name, sample_rate numeric, -- online scoring config
  grader_config jsonb, schedule_cron text, enabled bool, last_run_at null)
monitor_scores(id uuid pk, monitor_id fk, trace_id fk, scores jsonb, created_at)
```

**Required indexes** (high write volume + dashboard queries):
- `traces(project_id, started_at desc)` — the main list query.
- `spans(trace_id)`, `spans(project_id, started_at)`.
- `traces(project_id, status)`, `feedback(trace_id)`, `eval_results(run_id)`, `eval_results(run_id, item_id)` unique (per-item idempotency for eval-run resume).
- GIN on `traces.metadata` (jsonb_path_ops) for metadata filtering. Do **not** GIN-index span payloads (write amplification); span content is fetched by trace_id, not searched.

**Trace ingestion wire format** — define once in `packages/shared/trace-schema.json` and generate/validate both SDKs and the API against it. A batch upload is `{ "traces": [ {trace fields, "spans": [ … ]} ] }`. Trace and span IDs are client-generated UUIDs so ingestion is idempotent (`ON CONFLICT DO NOTHING` / upsert on completion events).

---

## 4. Auth Model (Phase 1 + 4)

Two credential types:
1. **SDK API keys** (`ak_<prefix>_<secret>`): per project. Store only SHA-256 hash + display prefix. Used by `/v1/ingest/*` and `/v1/feedback`. Resolved key → project_id; ingested data is force-stamped with that project_id regardless of payload contents.
2. **Dashboard sessions**: email+password (bcrypt) → signed HTTP-only session cookie (or JWT). Users belong to one org; all dashboard API queries join through `projects.org_id = current_user.org_id`. Keep auth simple — no OAuth/SSO/roles in v1; a user in an org can do everything in that org.

---

## 5. Build Phases

Each phase lists: **Goal → Steps → Acceptance criteria.** Verify acceptance criteria (run the commands, hit the endpoints) before moving on.

---

### Phase 0 — Scaffold & Infrastructure

**Goal:** Running skeleton: compose stack up, empty FastAPI app healthy, empty Next.js app rendering, git initialized.

**Steps:**
1. `git init`; add `.gitignore` (Python, Node, `.env`, `.next`, `__pycache__`, dist).
2. Create the repo layout from §2 (empty packages with minimal boilerplate).
3. `docker-compose.yml` with services: `postgres:16` (volume, healthcheck), `redis:7` with `--appendonly yes` + volume (buffered-but-unpersisted traces must survive a Redis restart, or Phase 2's zero-loss guarantee is void), `api` (build `apps/api`, depends_on healthy postgres/redis), `worker` (build `apps/worker`), `web` (build `apps/web`). Expose 5432, 6379, 8000 (api), 3000 (web). For dev, also support running api/web natively against dockerized postgres/redis — document both in README updates.
4. `.env.example`: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ANTHROPIC_API_KEY` (for judges), `OPENAI_API_KEY` (optional), `WEB_API_BASE_URL`.
5. `apps/api`: FastAPI app with `/healthz` (checks DB + Redis connectivity), CORS for the web origin, structured JSON logging.
6. `apps/web`: `create-next-app` (TypeScript, Tailwind, App Router, src dir). One page rendering "Anvil".
7. `Makefile`: `make up` (compose), `make api`, `make web`, `make worker`, `make test`, `make migrate`.

**Acceptance:** `docker compose up` brings up postgres+redis healthy; `GET :8000/healthz` returns `{"db": true, "redis": true}`; `:3000` renders. Committed.

---

### Phase 1 — Database Schema, Models, Auth

**Goal:** Full schema migrated; org/user/project/API-key CRUD working; tenant scoping enforced.

**Steps:**
1. Configure Alembic (async). Write migration(s) creating **all** tables and indexes from §3 up front (traces/spans/datasets/evals/feedback/alerts/monitors) — later phases then only touch code, not schema, except where a phase explicitly notes a migration.
2. SQLAlchemy models mirroring §3. Shared `Base`, uuid PKs (`uuid4` default), `created_at` server defaults.
3. Auth (`auth.py`):
   - `POST /v1/auth/signup` (creates org + first user), `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/me`. Session cookie, bcrypt via `passlib`.
   - FastAPI dependencies: `current_user()` (session) and `current_project_from_api_key()` (reads `Authorization: Bearer ak_…`, hashes, looks up, rejects revoked).
4. Project & key management: `POST/GET /v1/projects`, `POST /v1/projects/{id}/api-keys` (returns full key **once**), `DELETE /v1/api-keys/{id}` (revoke), `GET /v1/projects/{id}/api-keys` (prefixes only).
5. Seed script (`make seed`): demo org, user (`demo@anvil.dev` / `demo1234`), project, API key printed to stdout.
6. Tests: signup/login flow; cross-tenant access returns 404 (project of org B invisible to user of org A); revoked key rejected.

**Acceptance:** `alembic upgrade head` clean on fresh DB; seed works; all Phase-1 tests pass; manual curl of signup→create project→create key succeeds.

---

### Phase 2 — Ingestion Pipeline (API → Redis → Worker → Postgres)

**Goal:** High-throughput async trace ingestion with backpressure, durable via Redis Streams, batch-persisted to Postgres.

This is a core engineering challenge from the README — get the shape right:

**Steps:**
1. `POST /v1/ingest/traces` (API-key auth): accepts a batch (§3 wire format), validates against the shared JSON Schema (reject 422 with per-trace errors), stamps `project_id`, then `XADD` each trace (msgpack or JSON) to Redis Stream `ingest:traces`. Returns `202 {"accepted": n}` — **no DB write on the request path**. If Redis is unreachable, return `503` (SDKs retry). Additionally:
   - **Size limits:** reject request bodies > 5 MB (413). Truncate any single `input`/`output`/`attributes` field > 100 KB server-side, recording `{"anvil_truncated": true, "original_bytes": n}` in the span's attributes — never reject a whole batch for oversized content.
   - **Timestamp normalization (client clocks are untrusted):** clamp `ended_at < started_at` to zero duration; clamp span times outside the trace's `[started_at, ended_at]` window to its bounds; flag normalized spans with `anvil_clock_adjusted: true` in attributes.
   - **Tree sanity:** a `parent_span_id` that doesn't match any span in the trace (dangling or cyclic) is nulled out — the span becomes a root-level child rather than an error.
2. **Backpressure:** cap stream length with `XADD MAXLEN ~ 100000`. Before adding, if `XLEN > HIGH_WATER` (e.g. 80k), return `429` with `Retry-After` — SDKs will buffer/retry. Track drops/accepts via a lightweight counter (Redis INCR) exposed at `/v1/ingest/stats`.
3. Worker `ingest_consumer.py`: consumer group (`XREADGROUP`, group `persisters`, consumer name = hostname), read up to 500 messages or 200 ms (whichever first), batch-insert traces and spans with `INSERT … ON CONFLICT (id) DO UPDATE` — with two mandatory guards on the update clause:
   - **Tenant guard:** `… DO UPDATE SET … WHERE traces.project_id = excluded.project_id`. Trace/span IDs are client-supplied; without this, a client submitting another project's trace ID overwrites cross-tenant data. When the guard blocks an update, count it and log the offending project — do not error the batch. Same guard on spans.
   - **Monotonic completion guard:** with a consumer group, the `in_progress` and completed events of one trace can be processed out of order by different workers. Only update status/output/ended_at when the incoming row has `ended_at` set OR the existing row's `ended_at` is null — a completed trace must never regress to `in_progress`.
   Spans use `DO NOTHING` (plus the tenant guard via the insert's project_id). Then `XACK` + `XDEL`. On worker crash, pending entries are reclaimed via `XAUTOCLAIM` (idle > 60 s) at startup and periodically.
4. **Poison messages & abandoned traces** (same worker process, periodic tasks):
   - **Dead-letter queue:** before processing a reclaimed entry, check its delivery count (`XPENDING`). If ≥ 5, `XADD` it to `ingest:dlq` (capped MAXLEN 10k), `XACK` the original, and log — a malformed message must not crash-loop every worker replica. Expose DLQ depth in `/v1/ingest/stats`.
   - **Stale-trace reaper:** every 5 min, mark traces still `in_progress` with `started_at` older than 30 min as `status = 'abandoned'` (the customer's app likely crashed mid-trace; the completion event will never come). If a completion does arrive later, the monotonic guard lets it win — `abandoned` has no `ended_at`, so a real completion still applies.
5. Cost computation: worker-side. Maintain `anvil_worker/pricing.py` — a static table of $/1M input & output tokens per model (Claude, GPT, common OSS via providers); compute `cost_usd` per llm span and roll up totals onto the trace. Unknown models → cost null, never an error.
6. Read APIs (session auth) needed by the dashboard later:
   - `GET /v1/projects/{id}/traces` — cursor-paginated (by `started_at,id`), filters: time range, status, name, model, `metadata.<key>=<value>`, free-text on input/output (simple `ILIKE` on a text projection; no full-text engine in v1).
   - `GET /v1/traces/{id}` — trace + full span tree.
   - `GET /v1/projects/{id}/metrics/overview` — counts, error rate, p50/p95 latency, cost & token totals bucketed by hour/day (SQL `date_trunc` aggregates; add a covering index if slow).
7. Tests: end-to-end ingest → poll until worker persisted → read back identical tree; duplicate batch is idempotent; malformed span rejected with useful error; 429 path (set HIGH_WATER low in test); **cross-tenant trace-ID collision leaves the victim row untouched; completion processed before a late `in_progress` event does not regress; a poison message lands in the DLQ after 5 deliveries without blocking the stream; reaper marks a stale trace `abandoned` and a late completion still overrides it; oversized field is truncated with marker; negative-duration span is clamped.**

**Acceptance:** A script pumping 1,000 synthetic traces (5–10 spans each) through the HTTP endpoint fully persists with zero loss and zero duplicates while the worker is restarted mid-run (kill + restart — XAUTOCLAIM recovers). List/detail/metrics endpoints return correct data. Tests pass.

---

### Phase 3 — SDKs (Python first, then TypeScript)

**Goal:** A developer instruments their app in <10 lines and traces appear in Anvil. SDKs must **never crash or block the host app**.

**Shared design (both languages):**
- `init(api_key, base_url, flush_interval=2s, max_queue=1000)`.
- `trace(name, input=…)` context manager / wrapper creating a root trace; `span(name, type, …)` nestable via contextvars (Py) / AsyncLocalStorage (TS). Manual API too: `client.startTrace(...)`, `span.end(output=…)`.
- Decorator/wrapper sugar: `@observe(type="llm")` (Py), `observe(fn, {type})` (TS) — captures args as input, return as output, exceptions as error status.
- Background flusher: bounded in-memory queue → batch POST to `/v1/ingest/traces`; on 429/5xx exponential backoff with jitter; on full queue **drop oldest and count** (never block); flush on process exit (atexit / beforeExit).
- Client-side truncation mirroring the server policy (100 KB per field) so batches rarely hit the 5 MB body cap; a `redact` init option (`redact: (span) => span` / callable) lets customers scrub PII before anything leaves their process — document it.
- LangChain integration (Python): a `AnvilCallbackHandler(BaseCallbackHandler)` mapping LangChain run trees (llm/chain/retriever/tool starts+ends) to Anvil spans — this gives “instrument a LangChain app in one line” and is the demo path.
- Capture per span type: llm → model, params, prompt template + variables (if given), raw output, token usage from provider response; retrieval → query, returned chunks (text, source, score); tool → name, args, result.

**Steps:**
1. Build `sdks/python` (`httpx` for transport, zero heavy deps). Unit tests with a fake server (respx). 
2. Build `examples/demo-rag-app`: tiny FastAPI app doing fake-retrieval + a real (or mocked if no key) LLM call via LangChain, instrumented with the SDK + callback handler. This app doubles as the demo data generator: `python -m demo seed --n 200` produces varied realistic traces (some errors, some slow, varied models).
3. Build `sdks/typescript` mirroring the Python API (fetch-based, works in Node 18+; browser not required in v1). Vitest tests with msw.

**Acceptance:** Running the demo app and hitting its endpoint produces a multi-span trace visible via `GET /v1/traces/{id}` with correct parent/child nesting, tokens, and cost. Killing the Anvil API while the demo app runs does **not** break the demo app (verified in a test). Both SDK test suites pass.

---### Phase 4 — Dashboard: Traces & Waterfall (Next.js)

**Goal:** The core observability UI — login, project switcher, trace list with filters, and an explorable waterfall trace view.

**Steps:**
1. Web foundation: typed API client (`src/lib/api.ts`) using the session cookie; auth pages (login/signup); authenticated layout with sidebar (Traces, Datasets, Evals, Monitors, Alerts, Settings) and project switcher; middleware redirecting unauthenticated users.
2. **Traces list** (`/p/[projectId]/traces`): virtualized table (time, name, status badge, model, duration, tokens, cost, feedback icon), filter bar (time range, status, name, model, metadata key/value), cursor "load more", 10 s auto-refresh toggle.
3. **Trace detail** (`/traces/[traceId]`): the centerpiece —
   - Left: span tree (indented, collapsible). Right/main: **waterfall** — horizontal bars positioned/scaled by `started_at`/`ended_at` relative to the trace, colored by span type, with duration labels. Pure CSS/flex positioning is fine; no charting lib needed for the waterfall.
   - **Resilience:** the tree-builder must tolerate dirty data — orphan spans (unresolvable parent) render as root-level children, `in_progress`/`abandoned` traces and spans with null `ended_at` render as open-ended bars ("still running…" / "abandoned" badge), and truncated fields show a "content truncated at ingest" notice instead of broken JSON.
   - Click a span → detail panel: rendered input/output (pretty JSON with expand/collapse), prompt template with variables highlighted, retrieved chunks list, tokens/cost/latency, error + stack if failed.
   - Trace header: status, total latency/tokens/cost, model(s), session/user_ref, attached feedback; **"Add to dataset"** button (wired in Phase 5 — stub now).
4. **Overview page** (`/p/[projectId]`): metric cards (traces today, error rate, p95 latency, cost) + small time-series charts from the metrics endpoint (use `recharts`; **invoke the `dataviz` skill before writing chart code**).
5. Load the `frontend-design` skill before building the UI so it doesn't look like default Tailwind; dark-mode-friendly; empty states with copy-pasteable SDK setup snippet (show the project's key prefix).

**Acceptance:** With seeded demo data: login → browse list → filter by status=error → open a trace → waterfall renders correct proportions and nesting → span click shows prompt/variables/chunks. Works with a 100-span trace without jank.

---

### Phase 5 — Datasets & Evaluation Engine

**Goal:** Promote traces to datasets; run eval batches (deterministic + LLM-judge) through a job queue; view results.

**Steps:**
1. Dataset APIs: CRUD for datasets; `POST /v1/datasets/{id}/items` (manual add + bulk JSON upload); `POST /v1/datasets/{id}/promote` `{trace_ids: []}` — copies each trace's root input/output into items with `source_trace_id`. Wire the Phase-4 "Add to dataset" button (single + bulk select in the traces list).
2. **Graders** (`apps/worker/anvil_worker/graders/`):
   - Deterministic: `valid_json`, `contains` / `not_contains` (string or regex), `length_bounds` (chars or tokens), `exact_match`, `citation_present` (regex for `[n]`-style or URL). Each returns score 0/1 + detail.
   - LLM-as-judge via LangChain with **structured output** (`{score: 0..1, reasoning: str}`): built-in judge prompts for `accuracy` (vs expected_output), `hallucination` (vs provided context/chunks), `tone`, plus `custom` (user-supplied rubric prompt). Default judge model: `claude-sonnet-5` via `ANTHROPIC_API_KEY`; judge calls themselves traced with the Anvil Python SDK into a designated internal project (dogfooding — great demo moment). If no API key present, judges return `error: "no judge key configured"` gracefully.
3. **Job queue** (this is the README's "job queue for long-running eval batches" challenge): reuse Redis — `eval:jobs` stream + consumer group. `POST /v1/eval-runs` validates config, creates row (`queued`), enqueues. `eval_runner.py` worker: claims job → `running` → for each dataset item: produce target output (call the configured model/prompt via LangChain, filling `{{variables}}` from item input; or "re-score only" mode that grades existing outputs) → run all graders (bounded concurrency, e.g. 5; per-item try/except so one failure doesn't kill the run) → write `eval_results` row → increment `progress`. On completion compute `summary` (per-grader mean/pass-rate, cost, latency) into the run row. Support cancellation (status check between items). Additional required behavior:
   - **Crash recovery / resume:** a worker crash must not orphan a run in `running` forever. The runner heartbeats (`eval_runs.updated_at` or a Redis key) every ~15 s; at startup and periodically, reclaim runs whose heartbeat is stale > 2 min and **resume** them — the unique `(run_id, item_id)` index on `eval_results` makes per-item work idempotent: skip items that already have a result row.
   - **Item snapshot:** at run start, freeze the dataset's current item IDs into the run row (`eval_runs.summary` is for output; store the snapshot as `item_ids` inside `target_config` or a dedicated jsonb column added in the Phase-1 migration). The run iterates the snapshot, so items added/deleted mid-run or between runs don't shift the ground under it — Phase 6 depends on this.
   - **Provider rate limits:** target and judge calls retry on 429/overloaded with exponential backoff + jitter (cap ~60 s); after max retries the item records `error` and the run continues.
   - **Grader-error aggregation rule:** an item where a grader errored is **excluded from that grader's mean/pass-rate** (never scored as 0), and the summary reports `errored_items` per grader so degraded coverage is visible.
4. Run APIs: list runs, get run + results (paginated), `POST /v1/eval-runs/{id}/cancel`.
5. UI: Datasets pages (list, detail with items table, edit expected outputs); "New eval run" form (pick dataset → define target: model + prompt template textarea with `{{var}}` support + params → pick graders with per-grader config); run detail page with live progress (poll every 2 s), per-item results table (input, output, per-grader scores, expandable judge reasoning), and summary header.
6. Tests: deterministic graders unit-tested exhaustively; eval run e2e with mocked LLM (target + judge mocked via LangChain fake LLM) over a 10-item dataset — statuses, progress, results, and summary all correct; cancellation works; one item erroring doesn't fail the run; **killing the runner mid-run and restarting resumes from the snapshot without duplicating item results; a grader-errored item is excluded from that grader's aggregate.**

**Acceptance:** Full loop works in the UI: promote 10 demo traces → dataset → run eval with `valid_json` + judged `accuracy` → watch progress → see per-item scores and summary. Tests pass.

---

### Phase 6 — Run Comparison & Statistics

**Goal:** Side-by-side diff of two eval runs with statistically honest conclusions on small datasets (README: "computing statistically meaningful comparisons on small datasets").

**Steps:**
1. `apps/worker/anvil_worker/analysis.py` (Pandas/NumPy), used by API endpoint `GET /v1/eval-runs/compare?a=…&b=…` (runs must share a dataset):
   - Join results on `item_id`, **restricted to the intersection of the two runs' item snapshots** (the dataset may have gained/lost items between runs). The response includes `compared_items` vs each run's total, and the UI must surface it ("compared on 8 of 12 items") whenever the intersection is smaller than either run. Per grader: mean A, mean B, delta — computed over pairs where both sides have a non-errored score.
   - **Significance:** paired bootstrap on per-item score deltas (10k resamples → 95% CI of the mean delta) plus, for binary graders, an exact McNemar test on the discordant pairs. Report `significant: bool`, `ci: [lo, hi]`, `p_value`, and `n`.
   - Honest-messaging rule: when `n < 30` or CI contains 0, the API returns `verdict: "inconclusive"` with the CI — the UI must surface this ("not enough data to call it"), never a bare percentage.
   - Also compare cost and latency distributions (mean + p95 + delta).
2. UI `/evals/compare?a=&b=`: summary verdict banner in plain language ("v3 raised accuracy 71% → 84% [significant, CI +5..+21] but hallucination rate rose 3% → 9%"), per-grader table with deltas + CI, and a per-item diff view: rows where scores changed, showing both outputs side by side with the judge reasoning for each.
3. Add "Compare" entry points: from run list (select two) and from a run page ("compare against…").
4. Tests: analysis functions against hand-computed fixtures (including n=5 inconclusive case and a clearly significant case).

**Acceptance:** Comparing two seeded runs (seed script creates two runs with a known score shift) shows correct deltas, CI, and verdict; small-n case shows "inconclusive".

---

### Phase 7 — CI Gating

**Goal:** Prompt changes can be gated in CI instead of shipped on intuition.

**Steps:**
1. Add a minimal CLI to the Python SDK: `anvil eval run --dataset <id> --config eval.yaml --wait --min-score accuracy=0.8 --baseline-run <id>`. It creates a run via API, polls to completion, prints a results table, and **exits non-zero** if any threshold fails or (with `--baseline-run`) if any grader significantly regressed vs baseline (uses the compare endpoint).
2. `eval.yaml` format: target (model, prompt file path or inline, params) + graders list — documented with an example.
3. `examples/ci-gate/`: working GitHub Actions workflow: on PR touching `prompts/`, run `anvil eval run … --wait`, fail the check on regression.

**Acceptance:** Running the CLI locally against the demo project passes with a good threshold and exits 1 with a bad one; the example workflow is valid YAML and documented.

---

### Phase 8 — Live Monitoring, Drift Detection & Alerts

**Goal:** Continuously sample production traces, score them, detect regressions in cost/latency/error-rate/eval-score, and alert.

**Steps:**
1. **Monitors:** CRUD API + UI for `monitor_configs` (sample rate, graders — judge-only or deterministic, cron schedule). `sampler.py` worker loop (simple scheduler: check every 30 s for monitors due per `schedule_cron`, use `croniter`): sample N recent unscored traces (Bernoulli by sample_rate, cap per run), grade root input/output using the Phase-5 grader machinery (re-score mode), write `monitor_scores`.
2. **Alert evaluation:** `alerter.py` runs every 60 s: for each enabled `alert_rule`, compute the metric over the trailing window (SQL for cost/latency/error-rate; `monitor_scores` mean for eval_score), compare vs threshold; fire `alert_events` on breach (dedupe: don't re-fire while unresolved), resolve when back under. Notification channels v1: dashboard bell + webhook URL per project (POST JSON); log-only if none configured.
3. **Drift view** UI (`/monitors`): score-over-time chart per monitor (daily buckets), recent low-scoring sampled traces linked to their trace pages. **Alerts** UI: rule CRUD (metric, threshold, window), event history with fired/resolved timestamps.
4. Extend demo seeder with a "regression mode" (`--drift`) that emits traces with degraded quality/cost so alerts demonstrably fire.
5. Tests: sampler respects sample rate and doesn't double-score; alert fires on synthetic breach and resolves; webhook payload correct (respx-mocked).

**Acceptance:** With `--drift` seed data and a monitor + alert rule configured, the alert fires within one worker cycle and appears in the UI; webhook received.

---

### Phase 9 — Feedback Loop

**Goal:** End-user thumbs-down attaches to its originating trace and flows into datasets, so the regression suite grows from real failures.

**Steps:**
1. `POST /v1/feedback` (API-key auth): `{trace_id, rating, comment?, user_ref?}` → row + denormalized flag visible in trace list/detail. Two edge cases are mandatory:
   - **Feedback can race its trace:** an end-user may thumbs-down within a second of the response, while the trace is still in the Redis buffer. Don't insert synchronously — return `202` and route feedback through its own Redis stream (`ingest:feedback`); the consumer retries rows whose `trace_id` doesn't exist yet with backoff, giving up (→ DLQ) after 10 min. Tenant check on persist: the trace must belong to the key's project, else drop + count.
   - **Repeat feedback upserts:** same `(trace_id, user_ref)` updates rating/comment (the §3 partial unique index) — a user toggling 👎→👍 must not create two rows. Add `client.feedback(trace_id, rating, comment)` to both SDKs (and document exposing trace_id to the host app: `trace.id` is available from the SDK so the customer can round-trip it to their frontend).
2. UI: feedback badge + comment on trace header; traces list filter `feedback=negative`; bulk-select negative traces → "Add to dataset" (already built in Phase 5 — verify it composes); a "Failure inbox" view: recent thumbs-down traces not yet in any dataset (`left join dataset_items on source_trace_id`).
3. Demo app gets a thumbs-down button wired through the SDK.
4. Tests: feedback via API key attaches and is tenant-checked (trace must belong to the key's project); inbox query correct; **feedback submitted before its trace is persisted attaches once the trace lands; repeated feedback from one user_ref upserts to a single row.**

**Acceptance:** Demo flow works end to end: thumbs-down in demo app → appears in Failure inbox → promote to dataset → next eval run includes it.

---

### Phase 10 — Hardening, Docs & Demo Polish

**Goal:** Deployable, presentable, documented.

**Steps:**
1. Production compose profile: multi-stage Dockerfiles (slim images), `next build` standalone output, non-root users, healthchecks on all services, worker replicas ×2 (proving the consumer-group design), restart policies.
2. Sweep: consistent error responses (`{"error": {"code", "message"}}`), request-ID middleware + structured logs across api/worker, rate limit auth endpoints, pagination caps, 404-vs-403 tenant behavior consistent (always 404).
3. Load sanity check: seed 50k traces; confirm traces list, metrics, and a 100-span waterfall stay < 500 ms server-side (fix indexes/queries if not — `EXPLAIN ANALYZE`).
4. Rewrite `README.md`: what/why, architecture diagram (mermaid), quickstart (`docker compose up` → seed → login), SDK setup snippets (both languages), eval + CI-gate walkthrough, screenshots. Add per-package READMEs for the SDKs.
5. End-to-end demo script (`DEMO.md`): the 5-minute happy path — seed data → find a bad trace in the waterfall → thumbs-down inbox → promote to dataset → run two eval variants → comparison verdict → CI gate — matching the README's narrative.
6. Full test suite green; `make test` runs everything.

**Acceptance:** Fresh clone → `.env` from example → `docker compose up` → seed → complete `DEMO.md` walkthrough succeeds with no manual fixes.

---

## 6. Execution Guidance for the Implementing Agent

- **Order is dependency order.** Phases 0→5 are the critical path. 6–9 are independent of each other after 5 (any order); 10 is last. If time-boxed (hackathon), the minimum compelling demo is Phases 0–6.
- **Verify, don't assume.** Each phase's acceptance criteria are commands/flows to actually run. Use the demo app + seeder as your test harness throughout.
- **Keep routers thin.** Business logic lives in `services/` (API) and worker modules — the eval/grader machinery is shared between eval runs (Phase 5), monitors (Phase 8), and the CLI (Phase 7); build it once, reusable.
- **Idempotency everywhere on the write path:** client-generated IDs + upserts. Retries must never duplicate data.
- **Never trust payload tenancy.** `project_id` always comes from the resolved credential, never from the request body.
- **Mock LLMs in tests.** Real judge calls only in manual/demo flows; all automated tests use LangChain fake LLMs or respx mocks so the suite runs keyless and free.
- **When touching UI**, load the `frontend-design` skill first (and `dataviz` before any chart) as noted in Phases 4/6/8.
- Update this file's checkboxes/notes if reality forces a deviation, and record the reason inline so later phases inherit the decision.
- Before declaring any phase done, re-check §7 — every checklist item tagged with that phase must be implemented and tested.

**Explicitly out of scope for v1 (do not build):** data retention/TTL cleanup, project/org deletion cascades, GDPR-style delete-by-user_ref, OAuth/SSO/roles, browser-side TS SDK, full-text search engines. If any becomes unavoidable, note the deviation here.

---

## 7. Edge-Case Checklist

Cross-cutting failure modes the implementation **must** handle. Each is specified in detail in its phase; this list exists so no phase is marked complete with one of these missing.

| # | Edge case | Phase | Mechanism |
|---|-----------|-------|-----------|
| 1 | Cross-tenant overwrite via client-supplied trace/span IDs | 2 | Tenant guard on every upsert (`WHERE project_id = excluded.project_id`) |
| 2 | Out-of-order events regress a completed trace to `in_progress` | 2 | Monotonic completion guard on the upsert |
| 3 | Traces stuck `in_progress` after customer-app crash | 2 | Stale-trace reaper → `abandoned` (late completion still wins) |
| 4 | Poison message crash-loops the ingest worker | 2 | Delivery-count check → `ingest:dlq` after 5 attempts |
| 5 | Redis restart loses buffered traces | 0 | `--appendonly yes` + volume |
| 6 | Oversized payloads (multi-MB chunks) | 2, 3 | 5 MB body cap; 100 KB/field truncation with marker, client + server side |
| 7 | Client clock skew / negative durations / dangling parents | 2, 4 | Ingest normalization + orphan-tolerant waterfall renderer |
| 8 | Eval run orphaned by worker crash | 5 | Heartbeat + stale-run reclaim; resume via unique `(run_id, item_id)` |
| 9 | Dataset mutates between compared runs | 5, 6 | Item-ID snapshot at run start; compare on intersection, surface "n of m" |
| 10 | Provider rate limits mid-batch | 5 | Backoff with jitter; item-level error, run continues |
| 11 | Grader errors skewing aggregates | 5, 6 | Errored items excluded from means, `errored_items` reported |
| 12 | Feedback arrives before its trace is persisted | 9 | Feedback stream with retry window, then DLQ |
| 13 | Duplicate feedback from one end-user | 9 | Partial unique index + upsert |
| 14 | Non-UTC drift in crons/bucketing | all | UTC-everywhere convention (§2) |
| 15 | PII leaving the customer's process | 3 | SDK `redact` hook, documented |
