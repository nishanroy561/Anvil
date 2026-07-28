# Self-Observability — Prometheus, Grafana, OpenTelemetry (cross-cutting)

> Part of the [Anvil implementation plan](../plan.md). Compose services and ports are set up in Phase 0 ([architecture.md](architecture.md) §6); dashboards and alert verification land in Phase 11.

Anvil monitors its customers' LLM apps; this doc is about monitoring **Anvil itself**. Keep the two concerns separate: customer traces → Postgres + the Anvil dashboard; Anvil's own telemetry → Prometheus/Tempo/Grafana. Instrumentation is **built alongside each phase** — a component's metrics and spans ship in the same phase as the component; only the Grafana dashboards and alert-rule verification wait for Phase 11. Everything lives in `infra/observability/`, runs under the compose `observability` profile.

## 1. Metrics (Prometheus)

**API** (`apps/api`): `prometheus-fastapi-instrumentator` exposing `/metrics` — RED metrics per route. Custom series:
- `anvil_ingest_traces_accepted_total{project}`
- `anvil_ingest_rejected_total{cause="backpressure"|"rate_limit"|"validation"}` (the `quota` cause went with the withdrawn pricing model — ADR-0019)
- `anvil_ingest_truncated_fields_total`, `anvil_ratelimit_rejections_total{scope}`
- Backlog gauges `anvil_stream_depth` (XLEN) and `anvil_dlq_depth` — **deliberately exported by the API, not the worker**: a dead worker can't report the backlog it isn't draining, and these gauges are exactly what must stay visible in that failure.

**Workers** (`apps/worker`): each runs `prometheus_client.start_http_server` on its own port (9101 ingest, 9102 eval, 9103 sampler/alerter/retention; scrape targets in `prometheus.yml`). Key series — ingest: `anvil_consumer_lag` (XPENDING gauge), `anvil_persist_batch_seconds` (histogram), `anvil_traces_persisted_total`, `anvil_tenant_guard_blocks_total`; eval: `anvil_eval_items_graded_total`, `anvil_judge_call_seconds`, `anvil_judge_errors_total`; sampler/alerter: last-run timestamp gauges (staleness ⇒ scheduler dead).

**Alert rules** (`infra/observability/alerts.yml`): stream depth > high-water for 2 min; DLQ depth > 0; consumer lag monotonically growing 5 min; API 5xx rate > 1%; any worker target down or scheduler-staleness > 5 min. These catch the silent-death mode logs never surface: an ingest worker dying quietly while traces pile up in Redis.

## 2. Distributed tracing (OpenTelemetry)

- API + workers: OTel Python SDK, auto-instrumentation for FastAPI, SQLAlchemy, redis, httpx; OTLP → `otel-collector` → Tempo. Service names `anvil-api`, `anvil-worker-ingest`, `anvil-worker-eval`.
- **Context propagation across the queue:** inject `traceparent` into each Redis stream message at `XADD`, extract in the consumer — one distributed trace spans HTTP ingest → buffer → batch persist. This is the money view for "why is persistence slow".
- Next.js: skip OTel in v1 (its fetches already appear inside `anvil-api` traces); future item.
- Sampling: head-based `parentbased_traceidratio`, 10% on ingest (high volume), 100% elsewhere — env-configurable.

## 3. Grafana

Provisioned from committed JSON (no click-ops), port 3001, Prometheus + Tempo datasources. Two dashboards:
- **Anvil Ingestion** — throughput, stream depth vs high-water line, consumer lag, DLQ, 429s by cause, persist-batch latency, tenant-guard blocks.
- **Anvil API & Evals** — RED per route, rate-limit rejections by scope, judge latency/error rate, eval throughput.

## 4. Acceptance (verified during Phase 11's load test)

- Dashboards reflect the 50k-trace load live.
- A single ingest request is traceable API → worker → Postgres in Tempo.
- Stopping the ingest worker under load fires the **worker-target-down** and **stream-depth** alerts within their `for` windows. (Consumer lag/XPENDING *freezes* when a worker dies — nothing is being delivered — so the lag alert catches a *stuck* worker; target-down + stream-depth catch a *dead* one. That's why all three rules exist.)
