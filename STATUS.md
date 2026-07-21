# STATUS — implementation handoff

> **The current state of the build. Read this first, then `plan.md`, then the
> `plan/` doc for whatever you're implementing. Update this file whenever you
> complete meaningful work — it is the handoff between sessions and models.**

Last updated: 2026-07-22 (UI restyle session)

**UI restyle (2026-07-22, user-directed):** the web app moved from the dark
"iron & ember" theme to a light neutral system modeled on the user's reference
(opacity.com): `#F6F6F5` page, white surfaces, near-black Inter type, blue
`#3B76FF` accent, near-black primary buttons. Token *names* are unchanged
(iron/ink/line/ember are roles — see `tailwind.config.ts`), so all pages
restyled via the token remap; span/status/chart palettes were re-picked for
light backgrounds. Landing page (`app/page.tsx`) rebuilt in the same style
(pill CTAs, badge eyebrow, dark code card, CTA band) with scroll animation:
hero elements rise in sequence on load (`.rise`), below-fold sections fade up
on first viewport entry (`components/Reveal.tsx` + `.reveal`, IntersectionObserver,
no dependencies, reduced-motion shows everything instantly). Verified in-browser:
landing, login, traces list, trace waterfall, overview charts.

## Where we are

**Phase 0 (scaffold & infra): DONE and verified.**
**Phase 1 (schema, models, auth): DONE — acceptance criteria pass.**
**Phase 2 (ingestion pipeline): DONE — acceptance criteria pass (94 tests green
+ the 1k-trace worker-kill load test).**
**Phase 3 (SDKs): DONE — acceptance criteria pass (65 new tests + a live
API-kill run against the demo app).**
**Phase 4 (dashboard): BUILT — data-level acceptance verified over live HTTP;
the visual pass (waterfall proportions, 100-span jank check) still needs human
eyes on :3000.** User gave the UI go-ahead this session.
**Phase 5 (datasets & eval engine): SUBSTANTIALLY DONE, three gaps — see
"Phase 5 gaps" below. Backend (51 new tests) + UI are built, and the acceptance
loop ran over live HTTP: promote 10 demo traces → dataset → run with
valid_json/exact_match/length_bounds/accuracy → live progress → per-item scores
+ summary. But the *judged* half of that criterion has never actually scored:
this box has no ANTHROPIC_API_KEY, so `accuracy` recorded "no judge key
configured" ×10 (correctly excluded from means — mean=None, not 0).** User gave
the Phase 5 UI go-ahead.
Phases 6–11: not started (stubs with spec pointers exist).

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
- `cd sdks/python && python -m pytest -q` → **35 passed** (~9 s), no services needed.
- `cd sdks/typescript && npm install && npm test` → **24 passed** (~1 s); `npx tsc` builds clean.
- `cd examples/demo-rag-app && python -m pytest -q` → **6 passed** (~34 s; the
  mock LLM sleeps to make the waterfall look real).
- **Phase-3 acceptance, run live end to end** against the API + worker:
  `python -m demo seed --n 25` → all 25 persisted, 0 dropped, correct span tree
  (`rag_pipeline` → `retriever` / `ChatPromptTemplate` / `<model>`), tokens on
  the llm span, cost rolled up onto the trace, search/metadata/status/model
  filters all returning the right counts. Then the API was **killed with the
  demo app serving traffic**: 6 consecutive `/ask` calls returned 200 at
  unchanged latency (~0.59 s), traces buffered, and on restart the backlog
  drained by itself (sent 3→9, dropped 0).

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

### Phase 3 test suite

Python SDK (`sdks/python/tests/`) — no services, no keys, transport mocked with
respx and LLMs faked with langchain's test doubles:
- `test_tracing.py` — span parentage (incl. **concurrent asyncio tasks**, which
  is what contextvars buys), host exceptions marked *and* re-raised, `@observe`
  sync + async, manual `start_trace`/`start_span`, double-`end()` no-op,
  client-side 100 KB truncation (checklist row 6), unserializable values, and
  the 1000-span cap. Every emitted payload is validated against
  `packages/shared/trace-schema.json` — the same schema ingest enforces.
- `test_client.py` — ADR-0013 lives here: batching, drop-oldest with counting,
  API-down never raising into host code, 5xx retry then success, `Retry-After`,
  jittered capped backoff, 422 dropped rather than looped, requeue respecting
  the bound, shutdown with a dead API, and the three `redact` cases (scrub,
  drop-on-None, **drop-on-raise** — checklist row 15).
- `test_langchain.py` — chain/retriever/tool/LLM run trees → span trees, error
  propagation, token usage from both `llm_output` and `usage_metadata`, handler
  reuse starting a fresh trace, and a deliberately broken handler proving a
  callback bug costs a span, not the customer's request.

TypeScript SDK (`sdks/typescript/tests/`) — mirrors the Python suite (24 tests),
validated with Ajv against the same shared schema.

Demo app (`examples/demo-rag-app/tests/`) — multi-span trace shape, error
traces, the recursive redact hook, seeded-traffic variety, and the two
API-death scenarios.

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
- **SDK `stats()["queued"]` used to under-report.** The count was taken after a
  batch left the queue and before a failure put it back, so polling during an
  outage intermittently read 0 while traces were buffered — exactly the number a
  customer checks to answer "is Anvil losing my data?". Both SDKs now count
  in-flight traces as queued; regression tests in each. Found by watching the
  live kill test, not by the unit suite.
- Ajv's default build is draft-07; the shared schema is draft 2020-12, so the TS
  tests import `ajv/dist/2020.js`. The default import fails with a confusing
  "no schema with key or ref .../2020-12/schema".
- `python -m demo` takes its connection flags on either side of the subcommand
  (a shared parent parser), because `demo seed --api-key …` is what people type.
- The trigram index is confirmed usable (`EXPLAIN` shows a Bitmap Index Scan on
  `ix_traces_search_trgm`), but at ~1k rows the planner correctly prefers
  `ix_traces_project_started` and filters. Phase 11's 50k load test is where the
  trigram path gets verified under scale.

### Phase 4 — what was built and verified (2026-07-21)

- `apps/web` is now the real dashboard (Next.js 14 App Router, dark-only
  "iron & ember" design system in `tailwind.config.ts`; Space Grotesk display +
  IBM Plex Mono data faces via `next/font`):
  - **`/` is a public marketing landing** (not in plan/dashboard.md — added at
    the user's request, structured after umami.is: centered hero → product
    frame → six-up feature grid → snippet → footer). Its hero "screenshot" is a
    live-rendered waterfall using the real span palette, inside browser chrome.
  - `/login`, `/signup` (session cookie), `src/middleware.ts` cookie gate.
    Routing: `/` public; signed-in visitors bounce `/` → `/home`; `/home` is
    the authed redirect to the first project's traces.
  - Browser → API via the **`/api` rewrite proxy** (next.config.mjs) so the
    session cookie stays first-party; `WEB_API_BASE_URL` aims the proxy.
  - `/p/[id]/traces` — virtualized table (@tanstack/react-virtual), filter bar
    (free text ≥3 chars, time range, status, name, model, metadata k=v),
    keyset "Load more", 10 s auto-refresh toggle, empty state with the SDK
    snippet carrying a real key prefix.
  - `/traces/[id]` — the waterfall: shared tree/timeline rows, machinist ruler,
    span-type colors, **hot-span ember highlight** (longest closed span),
    orphan badge, open-ended "still running…"/"abandoned" bars, truncation
    notices (`anvil_truncated` markers), span panel with prompt-template
    variable highlighting, retrieval chunk list, error+stack, tokens/cost.
    Tree assembly in `src/lib/waterfall.ts` tolerates orphans/cycles/negative
    durations client-side (checklist row 7).
  - `/p/[id]` overview — 4 stat tiles + traces/errors stacked bars + cost area
    (recharts). Chart steps validated with the dataviz palette validator
    against the iron surface (UI status colors were too light; charts use
    darker `#2EA878/#D5544A/#DE6420`).
  - `/p/[id]/settings` — project info + API keys (list/create/revoke; full key
    shown once). Datasets/Evals/Monitors/Alerts are honest phase-labeled stubs.
- Backend: `TraceSummary` gained `project_id` (list/detail responses) so the
  detail page can link back; full API suite re-run green (69 passed).
- Verified over live HTTP (API + worker + `npm run dev`): login → cookie →
  list → `status=error` filter (3 traces) → detail span tree correct
  (`rag_pipeline` → retriever/prompt/llm); a synthetic **103-span dirty trace**
  (nested chains, one error span, one open-ended span, one orphan) ingested and
  served — detail API 24 ms, page 200 in 64 ms. Overview aggregates correct.
- **Not yet verified: the visual pass** — waterfall proportions/nesting by eye
  and the 100-span no-jank check need a human at :3000 (no browser tooling in
  the build session). `npm run build` is clean.
- Phase-4 leftovers: "Add to dataset" button is a disabled stub (Phase 5 wires
  it); feedback column shows "—" until Phase 9; `/v1/ingest/stats` has no
  session-authed variant so the dashboard doesn't surface ingest stats.

### Phase 5 — backend built and verified (2026-07-21)

- **Grader catalog is a shared contract.** `packages/shared/graders.json` is the
  single source of truth for grader names/kinds/config schemas, vendored into
  *both* services by `scripts/sync_schemas.py` (same pattern as
  `trace-schema.json`). The API serves it to drive the UI picker and validate
  run configs; the worker implements the graders. Neither imports the other
  (ADR-0014). `test_grader_catalog.py` fails on drift **and** on any
  catalogued-but-unimplemented (or implemented-but-uncatalogued) grader.
- `apps/api`: `routers/datasets.py` (CRUD, items, editable expected outputs,
  `POST /promote` — copies trace payloads, skips foreign/duplicate traces
  rather than failing the batch) and `routers/eval_runs.py` (create with both
  spend guards, list/get/results/cancel, `GET /v1/graders`). Enqueues to the
  new `eval:jobs` stream; a Redis failure leaves the run `queued` for the
  runner's sweep rather than 500ing.
- `apps/worker/graders/`: registry + 6 deterministic graders (`valid_json`,
  `contains`, `not_contains`, `length_bounds`, `exact_match`,
  `citation_present`) + 4 LLM judges (`accuracy`, `hallucination`, `tone`,
  `custom`) via LangChain `ChatAnthropic` structured output. Every grader call
  is timeout-wrapped; a raising or hanging grader costs one score, not the run.
- `apps/worker/eval_runner.py`: `eval:jobs` consumer group, item snapshot frozen
  at claim, bounded item concurrency (5), `updated_at` heartbeat + stale-run
  reclaim, cooperative cancellation between chunks, and `summarize()` applying
  the aggregation rule (grader-errored items excluded from that grader's mean,
  counted in `errored_items`).
- **Price table corrected.** STATUS previously flagged prices as unverified;
  Claude entries are now verified (Opus 4.8 was listed at $15/$75, actually
  $5/$25; Haiku 4.5 at $0.80/$4, actually $1/$5). Sonnet 5 ($3/$15) was already
  right and is the default judge model.
- Tests: **API 90 passed** (was 69), **worker 76 passed** (was 25). New coverage
  includes e2e 10-item run, resume-after-kill without duplicates, cancellation,
  one-item failure not sinking a run, snapshot immunity to mid-run dataset
  edits, orphan reclaim (and *not* stealing a live run), both spend guards, and
  cross-tenant 404s on every new route. All keyless — LLMs faked via
  `GradeContext.model_factory` / `EvalRunner(model_factory=…)`.
- **Phase 5 UI (same session, after go-ahead):**
  - `/p/[id]/datasets` — list + create/delete (409 `dataset_in_use` surfaced
    with a human message); detail page with a table of items, **click-to-edit
    input/expected-output cells** (JSON-or-text, save/cancel with failure
    feedback), per-item delete, source-trace links, add-item form, "Run eval"
    deep link.
  - `/p/[id]/evals` — run list with live 3 s refresh while anything is
    queued/running; `/evals/new` — three-step form (dataset → target mode
    rescore/generate → **grader picker rendered from `GET /v1/graders`**, so
    plugins appear with zero frontend changes; config forms generated from
    each grader's JSON Schema); judged-grader cost warning in the sticky
    submit bar.
  - `/p/[id]/evals/[runId]` — status, ember progress bar, 2 s poll while live,
    cancel button, per-grader summary cards (mean/pass-rate/errored), results
    table with per-grader score columns and expandable rows (judge reasoning,
    item errors, full output).
  - **"Add to dataset" wired both ways**: single-trace on the detail page and
    bulk via checkbox column + action bar on the traces list. One shared
    `AddToDataset` popover (pick dataset or create inline; shows
    promoted/skipped counts).
  - All six pages render 200 through the dev server; `tsc` clean. The
    dev-only artifacts from this check: dataset `acceptance-golden` (10 items)
    and run `acceptance-run` in the demo project.

### Phase 5 gaps (do these before calling the phase done)

1. **The live judge path has never executed.** Every judge test injects a fake
   model through `GradeContext.model_factory`, and the acceptance run had no
   key, so the real
   `ChatAnthropic(...).with_structured_output(Verdict).ainvoke()` call has
   never run once. evaluation.md §2's acceptance names judged `accuracy`
   explicitly. Set `ANTHROPIC_API_KEY` on the worker and re-run
   `acceptance-golden` to close this — a bug in `_build_model` would only
   surface there.
2. **Judge tracing / dogfooding is not implemented** (evaluation.md §2: judge
   calls traced with the Anvil Python SDK into a designated internal project).
   The worker has no `anvil-sdk` dependency and no internal-project config.
   Deferred deliberately, not half-built.
3. **No visual pass on the Phase 5 UI** — pages are confirmed to render 200 and
   typecheck, not to *look* right. Same outstanding item as Phase 4.

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
- `sdks/python` (`anvil-sdk` 0.1.0): `_wire.py` (Trace/Span + truncation +
  redact), `_client.py` (bounded queue, background flusher, backoff),
  `_tracer.py` (contextvars, `@observe`, manual API), `langchain.py`
  (`AnvilCallbackHandler`), README. Optional extras: `[langchain]`, `[dev]`.
- `sdks/typescript` (`@anvil/sdk` 0.1.0): same surface — `wire.ts`, `client.ts`,
  `tracer.ts` (AsyncLocalStorage), `index.ts`, README. ESM, Node 18+, builds to
  `dist/` with `tsc`.
- `examples/demo-rag-app`: FastAPI RAG app (`demo/app.py`) + fake KB
  (`demo/kb.py`) + mock-by-default LangChain model (`demo/chain.py`) + seeder
  (`demo/seed.py`) + CLI (`demo/__main__.py`). `DEMO_LLM=real` swaps in a live
  provider. Seeded models are all in the worker's price table so cost is
  non-null. Phase 8 adds `--drift` here; Phase 9 adds thumbs-down.
- `examples/ci-gate`, `examples/plugin-example`, `sdks/` docs beyond the above:
  stubs with spec pointers only.

## Next actions (in order)

1. **Finish Phase 4 acceptance**: have the user eyeball :3000 — waterfall
   proportions/nesting on a demo trace, the 103-span `acceptance_100_spans`
   trace for jank.
2. **Phase 6 — comparison & stats** (plan/evaluation.md §4):
   `anvil_api/services/analysis.py` (paired bootstrap + McNemar, intersection
   of item snapshots, "inconclusive" honesty rule), the compare endpoint, and
   the `/evals/compare` UI. The item snapshots Phase 5 freezes are the
   foundation it joins on.
3. Then Phase 7 onward per plan.md's phase table.

### Phase 3 leftovers worth knowing

- **`client.feedback()` is not implemented** in either SDK — it belongs to
  Phase 9 (evaluation.md §7), which owns the endpoint it would call. The pieces
  it needs are already in place: `trace.id` is a client-generated UUID exposed
  on the object and returned by the demo app's `/ask` response.
- **The eval CLI (`anvil eval run`) is not in the Python SDK yet** — that is
  Phase 7 (evaluation.md §5), which `plan/sdk.md` §0 notes is hosted here.
- The TS SDK's tests inject a scripted `fetchImpl` rather than using **msw**,
  which `plan/sdk.md` §2 suggested. Same coverage, one less dependency, and
  fully deterministic — but it is a deliberate deviation from the spec doc.
- The TS SDK is **not published and not wired into any workspace**; there is no
  root `package.json` referencing it. It builds and tests standalone.
- `MockChatModel` sleeps (350 ms, ×6 when "slow") to give the waterfall real
  proportions. That makes `demo seed --n 200` take a few minutes and the demo
  test suite ~34 s. Lower `latency_ms` if you just need rows in a table.

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

**The dev database is NOT empty after this session** (it was after Phase 2).
The demo project holds **39 traces** (36 ok / 3 error) from the Phase-3
acceptance runs — 25 from `demo seed --n 25` plus 14 from the two API-kill runs. They are realistic
multi-span traces and are useful for building Phase 4 against, so they were left
in place deliberately. Delete them if you want a clean slate; regenerate with
either generator:

```
python scripts/loadtest_ingest.py --traces 1000          # bulk, own tenant
cd examples/demo-rag-app && python -m demo seed --n 200  # realistic, nested
```

A second API key (`ak_68e1067e…`, named `phase3-demo`) was minted on the demo
project in the Phase-3 session, and a third (`ak_aca613d3…`, `phase4-acceptance`)
in the Phase-4 session. All are dev-only — revoke rather than reuse.

The demo project now holds **40 traces**: the 39 from Phase 3 plus the
103-span `acceptance_100_spans` trace (nested chains, one error span, one
open-ended span) ingested for the Phase-4 waterfall check.
