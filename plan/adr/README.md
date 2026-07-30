# Architecture Decision Records

Every load-bearing decision gets an ADR: short, immutable once accepted, superseded rather than edited. Subsystem specs describe *how* things work; ADRs record *why we chose this over that*, so decisions don't get relitigated by every new contributor (or coding agent).

**Process:** copy the template below into `NNNN-short-slug.md` (next number), fill it in, link it from the relevant `plan/` doc. Changing an accepted decision = new ADR with `Supersedes: NNNN`, old one marked `Superseded by: NNNN`. Statuses: Proposed → Accepted → Superseded/Deprecated.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-decisions-as-adrs.md) | Record architecture decisions as ADRs | Accepted |
| [0002](0002-redis-streams-for-buffer-and-jobs.md) | Redis Streams for ingestion buffer and job queue (not Kafka/Celery) | Accepted |
| [0003](0003-idempotent-ingestion-guarded-upserts.md) | Client-generated IDs + guarded upserts (tenant + monotonic guards) | Accepted |
| [0004](0004-postgres-jsonb-trace-store.md) | Postgres JSONB trace store; span payloads unindexed | Accepted |
| [0005](0005-trigram-search.md) | Trigram-indexed search projection (not FTS, not OpenSearch) | Accepted |
| [0006](0006-clickhouse-scale-path.md) | ClickHouse as the designated analytics scale path | Accepted (deferred to v2) |
| [0007](0007-fixed-window-rate-limiting.md) | Fixed-window Redis rate limiting, fail-open | Accepted |
| [0008](0008-small-n-statistics.md) | Paired bootstrap + McNemar with mandatory "inconclusive" verdict | Accepted |
| [0009](0009-self-observability-metric-ownership.md) | Prometheus/Grafana/OTel; API owns the backlog gauges | Accepted |
| [0010](0010-open-core-mit.md) | Open core: MIT license, `DEPLOYMENT_MODE` flag, no `ee/` fork | Superseded by 0019 |
| [0011](0011-cost-model-quotas-retention.md) | Cloud cost model: tier quotas + retention; batched deletes v1 | Superseded by 0019 |
| [0012](0012-plugin-system.md) | Entry-point plugin system; cloud loads built-ins only | Accepted |
| [0013](0013-sdk-never-blocks.md) | SDKs never block or crash the host app | Accepted |
| [0014](0014-worker-owns-its-write-sql.md) | Worker persists via explicit SQL; no dependency on `anvil_api` | Accepted |
| [0015](0015-sdks-emit-traces-at-completion.md) | SDKs emit a trace once, at completion; no client-side `in_progress` | Accepted |
| [0016](0016-model-name-picks-the-provider.md) | The configured model name resolves the LLM provider (Anthropic / Google) | Accepted |
| [0017](0017-judge-calls-traced-into-an-internal-project.md) | Judge calls are traced into a designated internal project, opt-in via `ANVIL_INTERNAL_API_KEY` | Accepted |
| [0018](0018-recorded-output-is-kept-apart-from-expected-output.md) | The recorded output is kept in `metadata.recorded_output`, apart from the reference `expected_output` | Accepted |
| [0019](0019-fully-open-source-no-commercial-tiers.md) | Fully open source: no plans, quotas or hosted tier; Phase 10 becomes retention | Accepted |
| [0020](0020-inference-hosts-are-addressed-with-a-routing-prefix.md) | Inference hosts get an explicit `groq/`-style routing prefix; extends 0016 | Accepted |
| [0021](0021-eval-apis-accept-either-a-session-or-an-api-key.md) | Eval/dataset APIs accept a session **or** an API key, so CI holds a scoped key | Accepted |
| [0022](0022-usage-rollups-are-monotonic.md) | `org_usage` counters only move up, so retention can't rewrite ingest history | Accepted |

## Template

```markdown
# ADR-NNNN: Title

Status: Proposed | Accepted | Superseded by NNNN · Date: YYYY-MM · Spec: ../doc.md

## Context
What forces are at play; what problem demanded a decision.

## Decision
What we chose, stated plainly.

## Consequences
What gets better, what gets worse, what we now must do.

## Alternatives considered
Each rejected option and the one-line reason.
```
