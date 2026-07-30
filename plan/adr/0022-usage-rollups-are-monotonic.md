# ADR-0022 — Usage rollups are monotonic, not a recount of what is still stored

Status: **Accepted** — 2026-07-30 · Spec: [../retention.md](../retention.md) §2

## Context

Phase 10 gives one worker two jobs that pull in opposite directions.

[retention.md](../retention.md) §1 deletes traces older than `RETENTION_DAYS`.
§2 fills `org_usage` with monthly rollups of traces ingested and eval items
graded, and says they are "recomputed from Postgres" — explicitly preferring a
daily recompute over Redis counters, because with no quota to enforce a
recompute is "simpler and always correct".

It is only always correct while nothing is deleted. Run both jobs together and
the recompute reads a table retention has already emptied:

- June ingests 400k traces. The rollup records 400k.
- `RETENTION_DAYS=30` passes. June's traces are gone.
- The next daily recompute counts June again, finds 0, and overwrites 400k with
  0.

The panel then reports that the quietest month on record was the month the
operator was busiest, and the number gets *less* true the longer Anvil runs.
Worse, the two features are independently reasonable — nothing in either one
looks wrong on its own, which is how this survives review.

The underlying problem is that `traces_ingested` was named for one thing and
computed as another. "Ingested" is a fact about the past. `SELECT count(*) FROM
traces` answers "how many are stored right now". They agree exactly until the
first retention sweep, which is late enough for the discrepancy to look like a
regression rather than a design flaw.

## Decision

**`org_usage` counters only ever move up. The daily pass upserts with
`GREATEST(stored, recomputed)`.**

The column means *ingested during that month*, which cannot fall. A recompute
that returns a smaller number is evidence of deletion, not of a correction, so
it is discarded.

Consequences accepted deliberately:

- **A month can be over-reported after a manual delete.** If an operator drops
  traces by hand — not via retention — the rollup keeps the higher figure. That
  is the right trade: the alternative silently rewrites history on every sweep,
  and this number drives an informational panel, never a bill or a limit
  ([ADR-0019](0019-fully-open-source-no-commercial-tiers.md)).
- **The first pass on an existing install under-reports months already expired.**
  Rollups begin from what is still in the database; traces deleted before the
  worker ever ran cannot be recovered. Backfilling would require data that no
  longer exists, so the honest floor is "what we can still see".
- **The current month is still fully recomputed** every pass, since it is being
  added to and `GREATEST` is a no-op while a count is climbing.

`storage_bytes` stays **null**. Per-org bytes cannot be attributed honestly —
`pg_total_relation_size` is per table, and a share-of-rows estimate would be a
guess wearing a byte count. Null is the same answer `pricing.py` gives for an
unknown model price, for the same reason.

## Alternatives rejected

- **Recompute and overwrite (what §2 literally says).** Simplest, and wrong the
  moment retention runs. Rejected once the interaction was noticed.
- **Redis counters incremented at ingest.** Immune to deletion, and explicitly
  dropped by ADR-0019 along with the quotas they existed to enforce. Adds a
  second source of truth that can drift, to solve a problem `GREATEST` solves in
  one clause.
- **Recompute only months the retention window cannot reach.** Correct, but
  couples the rollup to `RETENTION_DAYS` — change the window and previously
  frozen months start moving again. `GREATEST` needs no knowledge of the window
  and behaves correctly when it changes or is unset.
- **Rename the column to `traces_stored` and let it fall.** Honest, but answers
  a question nobody asked: an operator wants to know what their app produced,
  not what survived cleanup. The panel would also drop to near-zero for every
  month past the window, which reads as broken.
