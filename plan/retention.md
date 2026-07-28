# Retention & Housekeeping (Phase 10)

> Part of the [Anvil implementation plan](../plan.md). Schema (`org_usage`, FK cascades) in [architecture.md](architecture.md) §4.

## 0. Anvil is free software, fully featured, with no hosted tier

Decision record: [ADR-0019](adr/0019-fully-open-source-no-commercial-tiers.md), which supersedes [ADR-0010](adr/0010-open-core-mit.md) (open core) and [ADR-0011](adr/0011-cost-model-quotas-retention.md) (quotas + retention cost model).

Anvil is **MIT-licensed and complete**. There is no Free/Pro/Enterprise tier, no
metered quota, no upgrade path, and no hosted offering to upsell. Every install
is the whole product.

This deletes an entire class of work that earlier drafts of this phase
specified: plan definitions, monthly quotas, `quota_exceeded` at ingest, 80%
warning banners, per-endpoint plan gating, downgrade freezes, and the admin
plan-change API. None of it gets built. What survives is the part every
operator needs regardless of who is paying: **keeping the database from growing
forever.**

Two things that look like pricing but are not, and stay:

- **Per-trace LLM cost** (`pricing.py`, the model price table, cost roll-ups,
  the overview cost chart). This tells operators what *their own* model calls
  cost. It is the reason to run an observability tool, not a way to bill anyone.
- **Per-run spend guards** (`MAX_EVAL_ITEMS`, `MAX_CONCURRENT_RUNS` —
  [evaluation.md](evaluation.md) §3). Judge calls burn the operator's own
  credits; these are footgun protection against a mistyped loop, and are
  env-tunable rather than a cap someone pays to lift.

`organizations.plan` remains in the schema (migration `0001` is the complete v1
schema and is not amended for this) but carries no meaning. Treat it as
vestigial; do not read it in new code.

## 1. Retention worker (`retention.py`, daily)

The one thing this phase builds.

- A single global `RETENTION_DAYS` env var. **Unset is the default and means
  keep everything** — the worker idles. An operator who has not asked for
  deletion never gets deletion.
- Delete traces older than the window in batches (5k per transaction, brief
  sleep between) — never one giant `DELETE` that bloats WAL and holds locks.
- Spans, feedback and monitor scores follow via `ON DELETE CASCADE`.
  `dataset_items.source_trace_id` is `ON DELETE SET NULL`, so curated datasets
  survive retention: items carry their own copy of the input, the reference
  output, and the recorded output ([ADR-0018](adr/0018-recorded-output-is-kept-apart-from-expected-output.md)).
- Code comment: at real scale, monthly declarative partitions turn expiry into a
  partition drop. Explicitly not built in v1 ([architecture.md](architecture.md) §8).

## 2. Usage rollups (secondary)

`org_usage` exists in the schema and is cheap to fill, so the retention worker's
daily pass also upserts monthly rollups (traces ingested, eval items graded)
recomputed from Postgres. This drives a plain **Settings → Usage** panel: how
much is being ingested and how much judging is being spent, with no quota bar,
no percentage-of-limit, and no CTA.

There are no Redis usage counters and no nightly reconciliation job. Those
existed to enforce quotas cheaply at ingest time; with nothing to enforce, a
daily recompute straight from Postgres is simpler and always correct.

## 3. Tests & acceptance

Tests: retention deletes only out-of-window traces, and only for the org it is
sweeping; `source_trace_id` goes null while the dataset item still evaluates
correctly; the worker idles with `RETENTION_DAYS` unset; batching holds on a
volume larger than one batch.

**Acceptance (Phase 10):** with a synthetic 1-day window, traces older than the
window are deleted in batches, datasets built from them still run an eval to
completion, and unsetting `RETENTION_DAYS` leaves everything in place.
