# Ingestion Pipeline — API → Redis → Worker → Postgres (Phase 2)

> Part of the [Anvil implementation plan](../plan.md). Schema, auth, and the rate-limiter utility are in [architecture.md](architecture.md). The SDKs that feed this pipeline are in [sdk.md](sdk.md); the metrics this phase must export are in [observability.md](observability.md).

**Goal:** High-throughput async trace ingestion with backpressure, durable via Redis Streams, batch-persisted to Postgres. This is a core engineering challenge from the README — get the shape right. **No DB write on the request path.**

## 1. Ingest endpoint

`POST /v1/ingest/traces` (API-key auth): accepts a batch (wire format in architecture.md §4), validates against the shared JSON Schema (422 with per-trace errors), stamps `project_id` from the key, then `XADD`s each trace (msgpack or JSON) to Redis Stream `ingest:traces`. Returns `202 {"accepted": n}`. If Redis is unreachable → `503` (SDKs retry).

Input hygiene (client data is untrusted):
- **Size limits:** reject bodies > 5 MB (413). Truncate any single `input`/`output`/`attributes` field > 100 KB server-side, recording `{"anvil_truncated": true, "original_bytes": n}` in the span's attributes — never reject a whole batch for oversized content.
- **Timestamp normalization** (client clocks are untrusted): clamp `ended_at < started_at` to zero duration; clamp span times outside the trace's `[started_at, ended_at]` window to its bounds; flag adjusted spans with `anvil_clock_adjusted: true`.
- **Tree sanity:** a `parent_span_id` that matches no span in the trace (dangling or cyclic) is nulled — the span becomes a root-level child, not an error.

## 2. Backpressure & per-tenant rate limiting

Two separate mechanisms, both returning `429` + `Retry-After` (SDKs buffer/retry either way):

- **Global backpressure (protects Anvil):** cap stream length with `XADD MAXLEN ~ 100000`; before adding, if `XLEN > HIGH_WATER` (default 80k) → 429. The 20k gap between high-water and MAXLEN is headroom so approximate trimming never eats unprocessed entries.
- **Per-key rate limit (protects other tenants — noisy-neighbor guard):** reuse the architecture.md rate-limit dependency scoped per API key: default 300 ingest requests/min **and** 5,000 traces/min per key (env-configurable; generous enough that a legit app never hits them). Without this, one buggy client in a tight loop fills the shared stream and every other tenant queues behind it — global backpressure alone punishes the innocent.
- A third 429 cause, `quota_exceeded` (monthly plan quota), is added by [pricing.md](pricing.md) and checked here before `XADD`.

Track accepts/rejects (per cause, per key) via Redis counters exposed at `/v1/ingest/stats` so a throttled customer is diagnosable — and export every counter/gauge in this doc as a Prometheus metric per [observability.md](observability.md), built at the same time as the feature.

## 3. Consumer (worker `ingest_consumer.py`)

Consumer group (`XREADGROUP`, group `persisters`, consumer name = hostname); read up to 500 messages or 200 ms, whichever first; batch-insert traces and spans with `INSERT … ON CONFLICT (id) DO UPDATE` carrying **two mandatory guards**:

- **Tenant guard:** `… DO UPDATE SET … WHERE traces.project_id = excluded.project_id`. Trace/span IDs are client-supplied; without this, a client submitting another project's trace ID overwrites cross-tenant data. When the guard blocks, count + log the offending project — don't error the batch. Same guard on spans.
- **Monotonic completion guard:** with a consumer group, a trace's `in_progress` and completed events can be processed out of order by different workers. Update status/output/ended_at only when the incoming row has `ended_at` set OR the existing row's `ended_at` is null — a completed trace must never regress to `in_progress`.

Spans use `DO NOTHING` (+ tenant guard via inserted project_id). Then `XACK` + `XDEL`. On crash, pending entries are reclaimed via `XAUTOCLAIM` (idle > 60 s) at startup and periodically.

### Poison messages & abandoned traces (periodic tasks, same process)

- **Dead-letter queue:** before processing a reclaimed entry, check delivery count (`XPENDING`). If ≥ 5: `XADD` to `ingest:dlq` (MAXLEN ~10k), `XACK` the original, log. A malformed message must not crash-loop every replica. DLQ depth exposed in `/v1/ingest/stats` and as a metric.
- **Stale-trace reaper:** every 5 min, mark traces `in_progress` with `started_at` older than 30 min as `abandoned` (customer app died mid-trace; completion will never come). If a completion arrives later anyway, the monotonic guard lets it win — `abandoned` has no `ended_at`, so a real completion still applies.
- **Feedback stream:** this worker also consumes `ingest:feedback` (spec in [evaluation.md](evaluation.md) §7 — feedback can race its trace).

## 4. Derived fields (computed once at persist, never at query time)

- **Cost:** `anvil_worker/pricing.py` — static table of $/1M input & output tokens per model (Claude, GPT, common OSS); compute `cost_usd` per llm span, roll totals up onto the trace. Unknown model → cost null, never an error. The lookup goes through the pricing resolver registry ([plugins.md](plugins.md)) — the static table is the built-in resolver; self-hosters can add resolvers for custom/self-hosted models.
- **`search_text`:** lowercase concatenation of trace name + flattened text from root input/output, truncated to 4 KB. This is what free-text search runs against — span payloads are deliberately not searchable.

## 5. Read APIs (session auth; consumed by the dashboard)

- `GET /v1/projects/{id}/traces` — cursor-paginated (`started_at,id`); filters: time range, status, name, model, `metadata.<key>=<value>`, and free text via `search_text ILIKE '%q%'` backed by the trigram GIN index. **Never ILIKE over the JSONB columns** (seq scan; dies at a few hundred thousand rows). Free-text queries require a bounded time range (default last 7 days, max 30 — server-enforced) so cost stays capped when trigram selectivity is poor, and a 3-character minimum (trigram floor).
- `GET /v1/traces/{id}` — trace + full span tree.
- `GET /v1/projects/{id}/metrics/overview` — counts, error rate, p50/p95 latency, cost/token totals bucketed hour/day (`date_trunc` aggregates; covering index if slow; ClickHouse is the decided scale path — architecture.md §8).

## 6. Tests & acceptance

Tests: e2e ingest → poll until persisted → read back identical tree; duplicate batch idempotent; malformed span rejected with useful error; 429 backpressure path (low HIGH_WATER in test); cross-tenant trace-ID collision leaves the victim row untouched; completion processed before a late `in_progress` does not regress; poison message lands in DLQ after 5 deliveries without blocking the stream; reaper marks a stale trace `abandoned` and a late completion still overrides; oversized field truncated with marker; negative-duration span clamped; key A over its per-key limit gets 429 while key B ingests unaffected.

**Acceptance:** a script pumping 1,000 synthetic traces (5–10 spans each) through HTTP fully persists with **zero loss and zero duplicates while the worker is killed and restarted mid-run** (XAUTOCLAIM recovers). List/detail/metrics endpoints return correct data. All tests pass.
