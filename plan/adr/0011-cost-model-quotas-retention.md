# ADR-0011: Cloud cost model — tier quotas + retention windows; batched deletes in v1

Status: Accepted · Date: 2026-07 · Spec: ../pricing.md

## Context
Database cost grows linearly with traces. A cloud free tier without ingestion caps and expiry is an unbounded storage liability; retention is the cost lever, quotas are the abuse lever.

## Decision
Per-tier monthly trace quotas (Redis counters checked pre-`XADD`, nightly reconciled from Postgres) and per-tier retention windows enforced by a daily worker doing batched deletes (5k/txn with sleeps). Datasets survive retention via `source_trace_id ON DELETE SET NULL` (items copy their data). Cloud mode only (ADR-0010).

## Consequences
Unit economics are bounded per tier. Batched deletes are O(rows) — fine to low tens of millions; the decided upgrade is monthly declarative partitions (expiry = partition drop), noted in code but not built. Quota counters are best-effort between reconciliations (minor over/under-counting accepted).

## Alternatives considered
- **Partitioning from day one** — complicates every query and the ORM for a v1 that doesn't need it.
- **pg_cron TTL deletes** — same deletes, less control over batching/backoff, another extension.
- **No quotas, price on storage** — billing surprise machine; caps are kinder and simpler.
