# Anvil — Implementation Plan

> **Purpose:** The execution spine for building Anvil, an LLM observability + evaluation platform (see `README.md`). Written to be executed by an AI coding agent (or human) phase by phase. **Do not skip phases** — complete and verify a phase's acceptance criteria before starting the next. Deep specs live in `plan/` — this file tells you *what order and when done*; the subsystem docs tell you *how*.

## What we are building

A multi-tenant platform where engineering teams monitor, test, and debug the LLM features in their products: SDK-instrumented apps stream traces (full execution tree of one AI request) to FastAPI → Redis → Postgres, rendered as a waterfall in a Next.js dashboard; real traces are promoted into datasets and scored by deterministic checks + LLM judges; run comparisons are statistically honest; live traffic is sampled and scored continuously with drift alerts; end-user thumbs-down feedback flows back into datasets. Stack is fixed: FastAPI, PostgreSQL 16, Redis 7, Next.js 14+, Tailwind, LangChain/LangGraph, Pandas/NumPy, Docker (+ Prometheus/Grafana/OTel for self-observability).

**Licensing — fully open source:** MIT-licensed and complete. No tiers, no quotas, no feature gating, no hosted offering — every install is the whole product. Per-trace LLM cost tracking and per-run eval spend guards stay: they tell operators what their *own* model calls cost and stop a mistyped loop burning their credits, which is the product rather than billing. Spec: [retention §0](plan/retention.md), [ADR-0019](plan/adr/0019-fully-open-source-no-commercial-tiers.md).

## Subsystem specs (`plan/`)

| Doc | Owns |
|---|---|
| [plan/architecture.md](plan/architecture.md) | System shape, repo layout, conventions, **full DB schema**, auth + rate-limiter utility, Phase 0–1 steps, out-of-scope & scale-path decisions |
| [plan/ingestion.md](plan/ingestion.md) | Phase 2: ingest endpoint, backpressure + per-key limits, consumer guards, DLQ, reaper, derived fields, read APIs, search |
| [plan/sdk.md](plan/sdk.md) | Phase 3: Python/TS SDKs, LangChain callback, demo app |
| [plan/dashboard.md](plan/dashboard.md) | Phase 4: traces list, waterfall, overview, UI ground rules |
| [plan/evaluation.md](plan/evaluation.md) | Phases 5–9: datasets, graders, job queue, comparison stats, CI gating, monitors/alerts, feedback loop |
| [plan/retention.md](plan/retention.md) | Phase 10: retention worker, usage rollups |
| [plan/observability.md](plan/observability.md) | Cross-cutting: Prometheus metrics, OTel tracing, Grafana, alert rules |
| [plan/plugins.md](plan/plugins.md) | Cross-cutting: extension points (graders, alert channels, pricing), registry contract, isolation, cloud policy |
| [plan/adr/](plan/adr/README.md) | Architecture Decision Records — the *why* behind every load-bearing choice; read the index before questioning a design |

**Changing a subsystem? Edit its doc.** This file only changes when phase order, acceptance criteria, or the checklist change.

## Phases

Each phase's full steps + tests are in its spec doc; a phase is done when its acceptance criteria pass.

| # | Phase | Goal | Done when (acceptance) | Spec |
|---|---|---|---|---|
| 0 | Scaffold & infra | Compose stack up (incl. Redis AOF + observability profile), healthz, empty web, git | `docker compose up` healthy; `/healthz` true/true; `:3000` renders | [architecture §6](plan/architecture.md) |
| 1 | Schema, models, auth | Full schema migrated up front; auth + rate limiter; tenant scoping; seed | Clean migrate on fresh DB; seed works; cross-tenant 404 + limiter tests pass | [architecture §7](plan/architecture.md) |
| 2 | Ingestion pipeline | API→Redis→worker→Postgres with backpressure, guards, DLQ, reaper | 1k traces persist with zero loss/dupes through a mid-run worker kill; all edge tests pass | [ingestion](plan/ingestion.md) |
| 3 | SDKs | <10-line instrumentation, Py + TS, LangChain handler, demo app | Demo app yields correct multi-span trace; killing Anvil doesn't break the host app | [sdk](plan/sdk.md) |
| 4 | Dashboard | Login, trace list + filters, waterfall detail, overview | Seeded flow: filter → open trace → waterfall proportions/nesting correct; 100 spans no jank | [dashboard](plan/dashboard.md) |
| 5 | Datasets & eval engine | Promote traces; graders; job queue with resume, snapshot, spend guards | UI loop: promote 10 traces → run eval → live progress → per-item scores + summary | [evaluation §1–3](plan/evaluation.md) |
| 6 | Comparison & stats | Paired bootstrap + McNemar; inconclusive verdicts; diff UI | Seeded run pair shows correct deltas/CI/verdict; small-n reads "inconclusive" | [evaluation §4](plan/evaluation.md) |
| 7 | CI gating | CLI (`anvil eval run`) + example GitHub Action | CLI exit 0 with lenient threshold, exit 1 with strict; workflow documented | [evaluation §5](plan/evaluation.md) |
| 8 | Monitors & alerts | Scheduled sampling + scoring, drift charts, alert rules + webhook | `--drift` seed fires the alert within one cycle; webhook received | [evaluation §6](plan/evaluation.md) |
| 9 | Feedback loop | Thumbs-down → trace (race-safe) → failure inbox → dataset | Demo thumbs-down → inbox → promote → next eval includes it | [evaluation §7](plan/evaluation.md) |
| 10 | Retention & housekeeping | Batched retention worker (opt-in via `RETENTION_DAYS`); usage rollups panel | Synthetic 1-day window deletes out-of-window traces in batches; datasets built from them still evaluate; unset `RETENTION_DAYS` deletes nothing | [retention](plan/retention.md) |
| 11 | Hardening & polish | Prod images, sweeps, load test, docs, demo script | Fresh clone → compose up → seed → full `DEMO.md` walkthrough with no manual fixes | below |

**Order is dependency order.** 0→5 is the critical path; 6–9 are independent after 5 (any order); 10 needs 2 (traces to expire) and 5 (datasets that must survive expiry); 11 is last. Time-boxed? The minimum compelling demo is 0–6.

### Phase 11 detail (cross-cutting, so it lives here)

1. Production compose profile: multi-stage Dockerfiles, `next build` standalone, non-root users, healthchecks, worker replicas ×2 (proving the consumer-group design), restart policies.
2. Sweeps: consistent `{"error": {"code", "message"}}` everywhere; request-ID middleware + structured logs; pagination caps; 404-not-403 for other tenants; **rate-limit sweep** — verify every surface has one (auth, ingest per-key, eval spend guards, feedback) and add a ~240/min per-session backstop on dashboard reads. All limits env-configurable and documented in `.env.example`.
3. Load test: seed 50k traces; traces list, metrics, free-text search (verify via `EXPLAIN` it hits the trigram index, not a seq scan), and a 100-span waterfall each < 500 ms server-side. Run with the observability profile up — this doubles as [observability.md](plan/observability.md) §4's acceptance (dashboards live, Tempo end-to-end trace, worker-kill alert test).
4. Rewrite `README.md` as an open-source project front page (what/why, MIT badge, mermaid architecture, **`docker compose up` quickstart as the hero path** — it is the only path, and it yields the whole product — SDK snippets, eval + CI walkthrough, screenshots) + per-SDK READMEs.
5. Plugin loading ([plugins.md](plan/plugins.md) §5): entry-point discovery, `examples/plugin-example/` (grader + alert channel), `PLUGINS.md` author guide; isolation tests (raising/hanging plugin can't kill a run or worker; cloud mode ignores external plugins).
6. `DEMO.md`: the 5-minute path — seed → bad trace in waterfall → thumbs-down inbox → promote → two eval variants → comparison verdict → CI gate.
7. `make test` green across everything.

## Post-v1 backlog (deferred deliberately — add after Phase 11 completes)

Not part of v1 execution; do not build these during Phases 0–11. Listed so they're deferred, not forgotten. Rough priority order for an open-source launch:

1. **Contributor documentation** — `CONTRIBUTING.md`: dev setup (native vs compose), test conventions, PR expectations, the ADR process ([plan/adr/](plan/adr/README.md)), code style. First thing needed once the repo is public.
2. **Versioning policy** — SemVer across the repo; the SDKs and the plugin contract ([plan/plugins.md](plan/plugins.md)) are the public API surfaces where breaking changes hurt most: define deprecation windows for both, plus wire-format versioning for `/v1/ingest`. Record as an ADR when decided.
3. **Release process** — `RELEASING.md`: tagged releases, changelog, CI-published Docker images (api/worker/web), PyPI + npm SDK publishing, migration-compatibility notes per release (self-hosters upgrade on their own schedule).
4. **Production operations** — ops guide for self-hosters *and* cloud: backup/restore (Postgres + Redis AOF), upgrade runbook (migrations first, workers before API), scaling notes (worker replicas, Redis memory), incident runbooks keyed to the [observability](plan/observability.md) alerts (DLQ non-empty, consumer lag, worker down).
5. **Benchmarks** — reproducible harness + published numbers: ingestion throughput (traces/s at given hardware), end-to-end persist latency, dashboard query latency at 1M+ traces, eval throughput and judge cost per 100 items. Turns the Phase 11 load sanity check into public, regression-tracked numbers.

## Execution guidance

- **Verify, don't assume:** acceptance criteria are commands/flows to actually run. The demo app + seeder are your test harness throughout.
- **Idempotency everywhere on the write path:** client-generated IDs + guarded upserts; retries must never duplicate data.
- **Never trust payload tenancy:** `project_id` comes from the resolved credential, never the request body.
- **Mock LLMs in all automated tests** (LangChain fake LLMs / respx); real judge calls only in manual/demo flows.
- **UI work:** load the `frontend-design` skill first; `dataviz` before any chart.
- **Self-observability ships with each phase** — a component's metrics/spans land in the same phase as the component ([observability.md](plan/observability.md)); only dashboards/alert verification wait for Phase 11.
- Before declaring a phase done, re-check the checklist below for rows tagged with that phase.
- **Decisions are recorded, not relitigated:** before questioning a design choice, check [plan/adr/](plan/adr/README.md) — Accepted ADRs are settled. Any significant new decision or deviation made during the build gets its own ADR *at the moment it's decided*, linked from the relevant `plan/` doc. Out-of-scope list: [architecture §8](plan/architecture.md).

## Edge-case checklist

Cross-cutting failure modes that **must** be handled; each is specified in the linked doc. No phase is complete with one of its rows missing.

| # | Edge case | Phase | Mechanism (spec) |
|---|-----------|-------|------------------|
| 1 | Cross-tenant overwrite via client-supplied trace/span IDs | 2 | Tenant guard on every upsert ([ingestion](plan/ingestion.md)) |
| 2 | Out-of-order events regress a completed trace | 2 | Monotonic completion guard ([ingestion](plan/ingestion.md)) |
| 3 | Traces stuck `in_progress` after customer-app crash | 2 | Stale-trace reaper → `abandoned`; late completion still wins ([ingestion](plan/ingestion.md)) |
| 4 | Poison message crash-loops the worker | 2 | Delivery-count check → DLQ after 5 attempts ([ingestion](plan/ingestion.md)) |
| 5 | Redis restart loses buffered traces | 0 | `--appendonly yes` + volume ([architecture](plan/architecture.md)) |
| 6 | Oversized payloads (multi-MB chunks) | 2, 3 | 5 MB body cap; 100 KB/field truncation, client + server ([ingestion](plan/ingestion.md), [sdk](plan/sdk.md)) |
| 7 | Clock skew / negative durations / dangling parents | 2, 4 | Ingest normalization + orphan-tolerant waterfall ([ingestion](plan/ingestion.md), [dashboard](plan/dashboard.md)) |
| 8 | Eval run orphaned by worker crash | 5 | Heartbeat + reclaim; resume via unique `(run_id, item_id)` ([evaluation](plan/evaluation.md)) |
| 9 | Dataset mutates between compared runs | 5, 6 | Item-ID snapshot; compare on intersection, surface "n of m" ([evaluation](plan/evaluation.md)) |
| 10 | Provider rate limits mid-batch | 5 | Backoff + jitter; item-level error, run continues ([evaluation](plan/evaluation.md)) |
| 11 | Grader errors skewing aggregates | 5, 6 | Errored items excluded from means; `errored_items` reported ([evaluation](plan/evaluation.md)) |
| 12 | Feedback arrives before its trace persists | 9 | Feedback stream with retry window, then DLQ ([evaluation](plan/evaluation.md)) |
| 13 | Duplicate feedback from one end-user | 9 | Partial unique index + upsert ([evaluation](plan/evaluation.md)) |
| 14 | Non-UTC drift in crons/bucketing | all | UTC-everywhere convention ([architecture](plan/architecture.md)) |
| 15 | PII leaving the customer's process | 3 | SDK `redact` hook ([sdk](plan/sdk.md)) |
| 16 | Noisy tenant starves other tenants' ingestion | 2 | Per-key limits, separate from global backpressure ([ingestion](plan/ingestion.md)) |
| 17 | Auth brute-force / credential stuffing | 1 | Per-IP + per-email limits from day one ([architecture](plan/architecture.md)) |
| 18 | Unbounded judge spend | 5 | `MAX_EVAL_ITEMS` + `MAX_CONCURRENT_RUNS` ([evaluation](plan/evaluation.md)) |
| 19 | Feedback endpoint abuse from end users | 9 | Per-key rate limit ([evaluation](plan/evaluation.md)) |
| 20 | Ingest worker dies silently, backlog unnoticed | — | API-exported backlog gauges + Prometheus alerts ([observability](plan/observability.md)) |
| ~~21~~ | ~~Free tier as unbounded storage liability~~ | — | **Withdrawn** — no tiers ([ADR-0019](plan/adr/0019-fully-open-source-no-commercial-tiers.md)) |
| ~~22~~ | ~~Usage-counter drift~~ | — | **Withdrawn** — no Redis usage counters; rollups recompute from Postgres |
| 23 | Retention DELETE bloats WAL / holds locks | 10 | Batched deletes with sleeps; partitioning as scale path ([retention](plan/retention.md)) |
| 24 | Retention deletes a dataset's source trace | 10 | `source_trace_id ON DELETE SET NULL`; items copy data ([retention](plan/retention.md)) |
| ~~25~~ | ~~Downgrade silently destroys data~~ | — | **Withdrawn** — no plans to downgrade |
| 26 | Free-text search seq-scans at scale | 1, 2 | Trigram `search_text` + time bound + 3-char min ([ingestion](plan/ingestion.md)) |
| ~~27~~ | ~~Self-hoster hits a quota, plan gate, or upgrade nag~~ | — | **Withdrawn** — nothing is gated, so there is nothing to guard |
| 28 | Broken or hung plugin kills a worker, run, or alert delivery | 2, 5, 8, 11 | Per-call try/except + timeout; failures route through each subsystem's error path ([plugins](plan/plugins.md)) |
| 29 | Third-party code executing in a shared deployment | 11 | `DEPLOYMENT_MODE=cloud` loads built-ins only, checked at plugin discovery ([plugins](plan/plugins.md), ADR-0012) |

Withdrawn rows keep their numbers — several docs cite rows by number, and renumbering would silently re-point those references.
