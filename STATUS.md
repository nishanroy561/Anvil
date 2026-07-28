# STATUS — implementation handoff

> **The current state of the build. Read this first, then `plan.md`, then the
> `plan/` doc for whatever you're implementing. Update this file whenever you
> complete meaningful work — it is the handoff between sessions and models.**

Last updated: 2026-07-28 (Phases 6, 7 and 8 complete)

## Phase 8 — monitors & alerts (2026-07-28). DONE, acceptance passes.

- **`sampler.py`** grades live traffic on each monitor's cron. Three things
  carry the weight: a trace is scored **at most once per monitor** (the
  candidate query anti-joins `monitor_scores`, so a 10-minute monitor over a
  2-hour lookback does not re-grade and re-charge the same traffic twelve
  times); `last_run_at` is claimed with a **conditional UPDATE** before any
  grading, so two workers ticking in the same second cannot both pay for one
  run; cron is UTC, and an *unparseable* cron makes a monitor not-due rather
  than always-due — the other default turns a typo into a judge loop.
- **`alerter.py`** evaluates rules over a trailing window. No re-fire while an
  event is open. The event row is **committed before any channel is called**, so
  a dead webhook leaves `notified=false` — degraded and visible — instead of an
  alert that never existed. Grader errors are excluded from `eval_score` rather
  than averaged in as zeros, or a broken judge pages someone about a quality
  regression.
- **`channels.py`** is the third registry on the plugins.md contract, webhook as
  the built-in. One channel failing or hanging never costs the alert its others.
- API validates the two things that fail *silently*: an unparseable cron (a
  monitor that never runs looks exactly like one finding nothing wrong) and a
  grader config (`contains` with no value scores 1.0 forever).
- **`demo seed --drift`** degrades the answers without raising the error rate.
  Traces still come back `ok` and still cost money — nothing but grading
  notices, which is the whole argument for monitors.
- **UI** (after go-ahead): `/monitors` — cards with a drift chart, enable/disable,
  and lowest-scoring samples linked to their traces; the create form is
  registry-driven and warns when a **judge** is picked, since a monitor spends
  credits on a schedule, unattended, forever. `/alerts` — rule CRUD in plain
  language ("Eval score is below 0.5 over 120 min"), webhook config, and event
  history with a firing/resolved state and a "not delivered" marker.
- **Acceptance: `python scripts/drift_alert_acceptance.py` → PASSED**, 18 checks.
  Healthy traffic scores 1.0 and does not alert; drifted traffic scores 0.286,
  fires within one cycle, and a **real local HTTP server receives the webhook**
  carrying the rule and breaching value; a second cycle does not duplicate;
  recovery resolves and delivers `alert.resolved`. Runs the loops in-process so
  cycle boundaries are deterministic — waiting 60 s to observe "fires within one
  cycle" makes a flaky test, not a stronger one.

**One UI thing worth knowing:** the drift chart is width-capped (`max-w-2xl`)
on purpose. Stretched across a full-width card, even a 1.00 → 0.00 collapse
renders as a shallow near-horizontal line and reads as flat — I mistook it for
a missing line until I read the SVG path (`M42,6L1614,130`, correct all along).
A drift chart that makes a collapse look flat is worse than no chart.


## Phase 7 — CI gating (2026-07-28). DONE, acceptance passes.

- **`anvil eval run`** lives in the Python SDK (`anvil_sdk/cli.py`, entry point
  `anvil`), because CI already installs the SDK. Reads `eval.yaml`, creates a
  run, polls with `--wait`, prints a per-grader table, and gates.
- **The exit codes are the product**, and `1` vs `2` is deliberate: `0` pass,
  `1` a threshold failed or a grader *significantly* regressed, `2` the run
  never finished, `3` bad config/credentials/API. "Quality dropped" and "the
  eval never ran" are different conversations; a red check that conflates them
  gets ignored.
- **Three things fail the gate that look like passes**, all of which would
  otherwise go green while measuring nothing: a `--min-score` naming a grader
  absent from the run (a typo would silently disable the check), a grader where
  every item errored (`mean` is null, not zero), and a grader whose scores are
  entirely tautological (ADR-0018).
- **Inconclusive never blocks.** A gate that fires on noise is switched off
  within a week, and `inconclusive` is the honest reading (ADR-0008). Documented
  consequence: a 12-item dataset will essentially never block anything.
- `examples/ci-gate/` — commented `eval.yaml`, a working `workflow.yml` (PR
  paths filter, concurrency cancel-in-progress, PR comment, exit-code mapping),
  and a README. Both YAML files parse.
- **Acceptance: `python scripts/ci_gate_acceptance.py` → PASSED.** Six checks
  over live HTTP with the real CLI as a subprocess: lenient threshold (mean
  0.500 ≥ 0.4) → 0; strict (0.500 < 0.8) → 1; typo'd grader → 1; significant
  regression vs baseline → 1; n=5 with no movement → 0; bad API key → 3.
  No provider credits spent.
- **Prerequisite built first: the eval APIs had no API-key auth**
  ([ADR-0021](plan/adr/0021-eval-apis-accept-either-a-session-or-an-api-key.md)).
  They were session-cookie only, so CI would have had to store a *user's email
  and password*. Dataset and eval-run routes now accept either credential via a
  `Caller`; `deps.owned_project()` resolves both and stays the one place the
  "404, never 403" rule lives. The API key is checked **first** — a request
  carrying both must not be silently downgraded to whatever browser session is
  in the cookie jar — and a bad key 401s rather than falling through. Traces and
  projects stay session-only; nothing needs them from CI.
- **Groq is a supported provider**
  ([ADR-0020](plan/adr/0020-inference-hosts-are-addressed-with-a-routing-prefix.md)).
  Groq hosts models rather than making them, so ADR-0016's "family name implies
  provider" rule breaks: `llama-3.3-70b-versatile` says nothing about who serves
  it, and `openai/gpt-oss-120b` would route to OpenAI under any name-trusting
  rule. Inference hosts therefore take an explicit `groq/` routing prefix, of
  which only the first segment is stripped — so `groq/openai/gpt-oss-120b`
  reaches Groq as `openai/gpt-oss-120b`, and Groq's own `groq/compound` is
  addressed as `groq/groq/compound`. Prices verified against groq.com/pricing
  and keyed on the full prefixed name; `groq/compound` is deliberately unpriced
  (Groq passes cost through, so null is the honest answer). Verified live.

### Two bugs the Phase 7 acceptance caught that the unit tests could not

1. **The CLI crashed on Windows consoles.** It printed `→` and `—`, which
   `UnicodeEncodeError` on a cp1252 stream — so the gate died mid-run on any
   non-UTF-8 console. All printed strings are ASCII now; docstrings and comments
   were left alone since they are never written to a stream. The mocked tests
   captured to a `StringIO` and never touched an encoder, which is exactly why
   this needed a live run.
2. **A stale server invalidated the first acceptance attempt.** `pkill -f
   uvicorn` does not work on this Windows box (same for `next dev`), so a server
   started hours earlier still held :8000 and the "new" one silently failed to
   bind — the whole run was testing pre-ADR-0021 code and reporting 401s. Kill
   by PID via `Get-CimInstance Win32_Process` instead; `pkill` lies here.

## 2026-07-28 session (earlier — Phase 5 gaps, open source, Phase 6)

**Anvil is now fully open source — no plans, no quotas, no hosted tier**
([ADR-0019](plan/adr/0019-fully-open-source-no-commercial-tiers.md), superseding
0010 and 0011). Phase 10 was rewritten rather than deleted: `plan/pricing.md` →
`plan/retention.md`, and the phase is now the batched retention worker plus a
usage panel with no quota bar. Checklist rows 21, 22, 25 and 27 are withdrawn
(numbers kept — docs cite rows by number); 23 and 24 survive against retention.
Per-trace LLM cost tracking and the per-run eval spend guards **stay** — they
report what the operator's own calls cost, which is the product, not billing.
`DEPLOYMENT_MODE` survives with a narrowed meaning: it gates third-party plugin
loading on shared deployments and nothing else. `organizations.plan` is
vestigial and unread; no migration was written to drop it.

**The rescore/expected-output collision is fixed**
([ADR-0018](plan/adr/0018-recorded-output-is-kept-apart-from-expected-output.md)).
Promotion copied a trace's output into `expected_output` and nowhere else, and
rescore then graded `expected_output` itself — so `exact_match` and the
`accuracy` judge compared a value with itself. That is where every `mean 1.0`
in the Phase 5 acceptance runs came from; the earlier STATUS text framed it as a
caveat about reading the numbers, which was wrong — it was a missing field.
Promotion now writes `metadata.recorded_output` and rescore grades that.
Pre-ADR items keep the old fallback but their reference-comparing scores come
back marked `tautological`, surfaced on the summary cards, the score cells and
the expanded rows. **Existing promoted items are not backfilled** — re-promote
them, or backfill from `source_trace_id`.

Also closed this session: the three Phase 5 gaps were committed (they had been
sitting uncommitted), and the two remaining UI defects are fixed — dataset cells
that clipped mid-JSON now have a show more/less toggle, and a new amber
`status-warn` token carries the tautology warnings.

**Phase 6 is complete** — backend, acceptance and the `/evals/compare` UI (the
UI go-ahead was given and it was built the same session). See the Phase 6
section below.

**Two gaps closed while running the acceptance:**

1. **`mode="generate"` has now executed against a real provider.** It had only
   ever run against `"fake-model"` in unit tests. A 3-item run against
   `gemini-2.5-flash` produced output on 3/3 items, cost `0.000593` through the
   normal price table. Two things learned: **`gemini-2.5-flash-lite` is retired
   for new API users** (404 `NOT_FOUND`, "no longer available to new users") —
   it is still in `pricing.py`, correctly, since existing users keep it, but do
   not reach for it as the cheap default. And the model wrapped its JSON in
   markdown fences, so `valid_json` scored 0.0 — the grader is right and the
   model is the problem, which is a fair advertisement for the product.
2. **The API never validated grader configs**, despite
   `services/graders_catalog.py` documenting that it did. Found by falling into
   it: the acceptance script passed `{"text": ...}` where `contains` wants
   `{"value": ...}`, and an empty needle matches every string, so **both runs
   completed with a confident `mean 1.0` having measured nothing**. That is the
   worst failure shape this product has — a silent pass. `POST /v1/eval-runs`
   now validates each builtin grader's config against the catalog's JSON Schema
   and 400s `invalid_grader_config`. Unknown graders stay unvalidated (they may
   be worker-side plugins the API cannot see).

**Test counts: API 156, worker 156, Python SDK 60, demo 6.** All green with
Postgres/Redis up; `npx tsc --noEmit` and `npm run build` clean in `apps/web`.

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
+ summary. The *judged* half of that criterion scored nothing at the time —
`accuracy` recorded "no judge key configured" ×10 (correctly excluded from
means — mean=None, not 0). As of 2026-07-24 the live judge call is proven
against Gemini at the unit level; the judged full-stack rerun is still to do.
See "Phase 5 gaps" #1.** User gave the Phase 5 UI go-ahead.
**Phase 6 (comparison & stats): DONE** — acceptance passes.
**Phase 7 (CI gating): DONE** — acceptance passes.
**Phase 8 (monitors & alerts): DONE** — acceptance passes.
Phases 9–11: not started (stubs with spec pointers exist).

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
- **The Gemini free tier is small enough to hit inside one eval run.** A second
  10-item judged run on the same day died partway with `RESOURCE_EXHAUSTED`
  (`generate_content_free_tier_requests`), ~16 calls in. Handled correctly and
  worth seeing: 429 is retryable, the backoff exhausted, 4 items recorded
  grader errors, the run still **completed**, and the aggregation rule excluded
  them (`mean 1.0` over `scored_items 6`, `errored_items 4`) rather than
  averaging zeros. Budget for it when planning Phase 6's two-run comparison —
  that needs ~2× the judged calls and will not fit in a free-tier day alongside
  other runs.
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

1. ~~**The live judge path has never executed.**~~ **CLOSED — and this gap's
   text was already stale when written.** The eval-run history in the dev DB
   shows the judge ran live on **2026-07-21**, in the Phase 5 session itself:
   `judged-acceptance (gemini)` (18:09, `gemini-2.5-flash`, 7 scored / 3
   errored, cost 0.0) and `judged-acceptance v2 (budget+cost)` (18:15, 7 scored
   / 3 errored, **cost 0.004256**). The run that gap #1 actually described is
   the earlier `acceptance-run` (16:22), whose `accuracy` config named **no
   model** — so ADR-0016's fixed order resolved it to the box's dead Anthropic
   key and all 10 items errored. The conclusion drawn from it ("the real
   `ainvoke()` has never run once") was wrong by the end of that same session,
   and nobody corrected it. **Check the run history before trusting a claim
   like this again.**
   Re-verified 2026-07-24 anyway, which was worth doing for the two pieces the
   Jul 21 runs don't evidence. A keyless-path unit check with no `model_factory`
   injected — `build_chat_model → with_structured_output(Verdict,
   include_raw=True) → ainvoke()` through `run_grader("accuracy", …)` — scored
   a matching answer 1.0 and a contradicting one 0.0, which is the only
   evidence anywhere that the judge *discriminates* rather than returning a
   constant. And a full-stack run (`judged-acceptance-1784852084`, id
   `d2ed23c7`) completed 10/10 with `errored_items 0` and `total_cost_usd
   0.006571`.
   **Read those 1.0 means with care:** they are `rescore` runs over promoted items, so
   `expected_output` was copied from the very output being graded and a perfect
   score is near-tautological. They demonstrate the *pipeline*, not judge
   quality. The discrimination evidence is the smoke test above (1.0 vs 0.0),
   not any of these runs. A run that would genuinely exercise judgement needs
   either `mode="generate"` against a real target model or hand-edited expected
   outputs — worth doing when Phase 6 needs two runs that actually differ.
   Note when configuring any judged run: both keys are set in `.env`, and
   ADR-0016's fixed order puts Anthropic first, so a judge with **no model
   named** resolves to the dead Anthropic key and reproduces the original gap.
   Name `gemini-2.5-flash` explicitly.
   Prereq now in place: `apps/worker/pyproject.toml` gained a `[judges]` extra
   (`langchain-anthropic`, `langchain-google-genai`, `python-dotenv`) — the
   provider packages were previously not declared anywhere and so were
   uninstallable; `.env.example` documented a nonexistent `OPENAI_API_KEY` and
   omitted `GOOGLE_API_KEY`. Both fixed.
2. ~~**Judge tracing / dogfooding is not implemented.**~~ **Done 2026-07-24 —
   see ADR-0017.** `apps/worker/anvil_worker/dogfood.py` traces every judge call
   through the Anvil Python SDK into the project named by
   `ANVIL_INTERNAL_API_KEY` (`ANVIL_INTERNAL_BASE_URL` aims it). Opt-in: unset
   is the default and does nothing, so the keyless suite and a bare checkout are
   unaffected. Shape is one trace per judge call, `judge:<grader>`, wrapping one
   `llm` span named after the model — tokens live on spans, so the two levels
   are what make judge spend visible at all. The trace covers the whole call
   *including retries*. A dedicated `AnvilClient` is passed explicitly rather
   than `anvil.init()`, so nothing else in the worker can accidentally ship.
   Every failure path (no SDK, construction raising, SDK raising, dead API)
   degrades to an inert recorder — 12 tests in `tests/test_dogfood.py` pin that
   telemetry can never change a score.
   **Verified live**, not just unit-tested: a judged run against the new
   `Anvil Internal` project produced 10 `judge:accuracy` traces whose statuses
   matched the run exactly (6 ok / 4 errored), carrying the prompt, the verdict,
   the provider's own 429 text on the failures, and on the successes
   `total_tokens 190/245` → `total_cost_usd 0.000669` through the normal
   `derive.py` roll-up.
   Note: `anvil-sdk` is imported lazily and is *not* a declared worker
   dependency — it is unpublished and installs in-tree
   (`pip install -e sdks/python`). A key set with no SDK present logs a warning
   rather than failing silently.
3. **Visual pass done 2026-07-24** (Chrome, signed in as the demo user), for
   both the Phase 4 leftover and the Phase 5 pages. **Awaiting the user's own
   sign-off** — this was an agent's eyes, not a human's.

   **Phase 4 acceptance criterion passes.** On the 103-span
   `acceptance_100_spans` trace: proportions correct (`stage_0` at 11.00 s ends
   exactly on the 12 s gridline, `stage_1` starts there, `chain_*_9` at 6.50 s
   is visibly ~⅗ of an 11 s bar), nesting/indentation correct, the five span
   types colour-distinct, duration labels flipping to the left of bars near the
   right edge, and **no jank** scrolling the full 103 rows. `background_flush`
   renders "still running…" with a fading gradient; the span panel shows type,
   id, timing, input and output.

   **Phase 5 pages read well.** The eval-run detail is the strongest: summary
   cards show `1.00` *and* `4 errored` in red side by side rather than hiding
   failures, errored rows carry ~38 s latency (visibly the retry backoff), and
   expanding a scored row shows judge reasoning plus pretty-printed full output.

   Four defects found. **(a), (b) and (c) are fixed and verified; (d) is
   logged.**

   a. **FIXED.** *The orphan badge could never render for ingested data.*
      `waterfall.ts:64`
      sets `orphan` only when `parent_span_id` is *set but unresolvable*, while
      Phase 2 ingest normalization **nulls dangling parents** before storage
      (`test_normalize.py`). Verified on the acceptance trace: the deliberately
      orphaned `orphaned_callback` span arrives with `parent_span_id=None`, so
      it renders as a plain root with no badge. Two individually correct
      behaviours cancelling out — the server repairs the damage and destroys the
      evidence, so "had a parent we never saw" is indistinguishable from "is a
      root". Fixed on the ingest side, where the information still exists:
      `normalize.py` now sets `anvil_orphaned` on the span's attributes as it
      nulls the link (same convention as `anvil_clock_adjusted`), and
      `waterfall.ts` badges on that flag *or* the original client-side check —
      so payloads that bypass normalization keep working. Three tests in
      `test_normalize.py` cover dangling, self- and cyclic parents plus the
      case that matters most: a **genuine root is not marked**, or the badge
      cries wolf on every trace. Verified end to end with a freshly ingested
      `orphan_badge_check` trace — the badge appears on `lost_parent_span` and
      on neither the real root nor its valid child.
   b. **FIXED.** *Grader-level errors were unreachable in the UI.* In
      `evals/[runId]/page.tsx:60`, `hasDetail` counted only `result.error` (the
      *item* failed) and judge reasoning — not `result.scores[name].error` (a
      *grader* failed). So the four 429-failed rows get no chevron and don't
      expand, and the expanded panel (lines 104–135) has no branch to render
      grader errors anyway. The provider's message survived only as a `title`
      tooltip on the `err` cell — a ~400-char error in a native tooltip, i.e.
      the most diagnostic thing in the run was the hardest thing to read.
      `hasDetail` now also counts `scores[*].error`, and the expanded panel
      renders each as "<grader> — grader error". Verified: the four 429 rows
      expand and show the full `RESOURCE_EXHAUSTED` text.
   c. **FIXED** (folded into b). Result rows expanded via `onClick` on a bare
      `<tr>` — no role, `tabIndex`, or key handler, so the only route to a
      judge's reasoning was invisible to the accessibility tree and unusable by
      keyboard. Now `role="button"`, `tabIndex`, `aria-expanded`, and
      Enter/Space.
   d. **Open (cosmetic):** dataset detail clips expected-output cells mid-JSON
      with no "show more" affordance. Left for a polish pass.

### Phase 6 — comparison & stats (2026-07-28)

**Backend done; acceptance passes. UI not started (needs go-ahead).**

- `apps/api/anvil_api/services/analysis.py` — pure functions over dicts, no ORM
  and no I/O, which is what lets the tests use hand-computed fixtures. Paired
  bootstrap (10k resamples, fixed seed so a page refresh doesn't move the
  interval), exact McNemar over discordant pairs for graders whose scores are
  all 0/1, latency and cost deltas. In the API because compare is synchronous
  and the API must not import from `apps/worker` (ADR-0014).
- **The refusals are the substance.** A grader returns `inconclusive` with a
  `reason` for: `no_paired_scores`, `insufficient_data` (n < 30),
  `ci_contains_zero`, and `tautological` (both sides graded against their own
  output — ADR-0018; a zero delta there would report "no regression" when
  nothing was measured). Grader errors drop the pair rather than scoring 0, so
  a provider timeout can't invent a regression.
- `GET /v1/eval-runs/compare?a=&b=` — **declared before `/eval-runs/{run_id}`**;
  FastAPI matches in declaration order and the other way round parses "compare"
  as a run id and 422s. There is a regression test pinning this. Runs over
  different datasets 400 `datasets_differ`; other tenants 404.
- `numpy` is a new API dependency. **pandas deliberately is not** — the spec's
  "(Pandas/NumPy)" is a toolset note, and this is vectorized array math with no
  dataframe in it. Same class of deliberate deviation as the TS SDK's
  `fetchImpl` over msw.
- Tests: 28 new (`test_analysis.py` 18, `test_compare.py` 10). Includes the
  n < 30 boundary at 29/30, noise straddling zero, a run compared with itself
  reading `inconclusive` rather than "no regression", McNemar against
  hand-computed values (`2·11/1024`, `2/2³`), and CI reproducibility.
- **Acceptance: `python scripts/compare_acceptance.py` → PASSED**, against a
  live API + worker. n=40 real shift → `regressed`, delta −1.0, CI [−1.0, −1.0],
  p = 1.82e-12 (= 2/2⁴⁰, hand-checkable); the same shift at n=5 →
  `inconclusive` / `insufficient_data`, p = 0.0625 (= 2/2⁵); a run against
  itself → `inconclusive`. **No judge credits spent** — the shift is built from
  the deterministic `contains` grader, since the statistics don't care where
  scores come from.

**Phase 6 UI (same session, after go-ahead) — built and checked in Chrome:**

- `/p/[id]/evals/compare?a=&b=` — verdict banner (one plain-language sentence
  per grader), per-grader table (mean A/B, delta, 95% CI, p, n, verdict chip,
  and a `status-warn` note where any pairs were tautological), latency and cost
  cards, and a per-item diff showing only rows whose score changed, both
  outputs side by side with judge reasoning.
- **The banner never leads with a percentage when the verdict is
  inconclusive** — that is ADR-0008's requirement, not a style preference.
  Verified on screen: the n=5 comparison reads "contains: not enough data to
  call it — 5 items compared, and it takes at least 30 before a difference this
  size can be told apart from chance", while the n=40 one reads
  "n40-B-answering lowered contains 100% → 0% … significant, CI -1.00 to -1.00".
  The delta is still in the table either way; only the headline changes.
- A "compared on n of m items" line always shows, plus an explicit warning when
  `partial` — the dataset changed between the runs.
- Entry points: checkbox column + selection bar on the run list (capped at two;
  a third selection drops the oldest, and the Compare button stays disabled with
  an explanation when the two runs scored different datasets), and a
  "Compare against…" popover on the run detail page listing only runs over the
  same dataset. The popover closes on Escape and on outside click.
- Route note: `/evals/compare` and `/evals/[runId]` coexist because Next.js
  prefers the static segment. This is the opposite of the API, where FastAPI
  matches in declaration order and `compare` had to be declared first.
- `npx tsc --noEmit` clean; `npm run build` clean.

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
  `pricing.py` is **partly verified**: Anthropic entries were checked against the
  published list on 2026-07-21 and Google's were added under ADR-0016, but the
  two OpenAI entries are still marked `# placeholders — verify` in the file.
  Don't make a user-facing cost claim on a `gpt-4o` trace. (Earlier STATUS text
  called the whole table unverified in one place and corrected in another;
  neither was right.)
- `apps/web`: the full dashboard — see the Phase 4 and Phase 5 sections above
  for what it contains. (This entry previously described a placeholder page with
  `npm install` not yet run; that was two phases out of date.)
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
   trace for jank. Worth a look at `/evals/compare` while there.
2. **Phase 9 — feedback loop** (plan/evaluation.md §7): the `ingest:feedback`
   stream is declared in both `streams.py` files with no consumer, and
   `client.feedback()` is missing from both SDKs. Also needs the failure inbox
   and a thumbs-down button in the demo app.
3. Then 10 (retention) and 11 (hardening) per plan.md's phase table.

**Backfill worth doing at some point:** items promoted before ADR-0018 have no
`metadata.recorded_output`, so their reference-comparing scores come back
`tautological`. Re-promote them or backfill from `source_trace_id`.

### Phase 3 leftovers worth knowing

- **`client.feedback()` is not implemented** in either SDK — it belongs to
  Phase 9 (evaluation.md §7), which owns the endpoint it would call. The pieces
  it needs are already in place: `trace.id` is a client-generated UUID exposed
  on the object and returned by the demo app's `/ask` response.
- ~~The eval CLI is not in the Python SDK yet~~ — **built in Phase 7**;
  `anvil_sdk/cli.py`, entry point `anvil`, extras `[cli]` for PyYAML.
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
