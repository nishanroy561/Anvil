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
| [0010](0010-open-core-mit.md) | Open core: MIT license, `DEPLOYMENT_MODE` flag, no `ee/` fork | Accepted |
| [0011](0011-cost-model-quotas-retention.md) | Cloud cost model: tier quotas + retention; batched deletes v1 | Accepted |
| [0012](0012-plugin-system.md) | Entry-point plugin system; cloud loads built-ins only | Accepted |
| [0013](0013-sdk-never-blocks.md) | SDKs never block or crash the host app | Accepted |

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
