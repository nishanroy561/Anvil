# ADR-0019 — Anvil is fully open source, with no commercial tiers

Status: **Accepted** — 2026-07-28 · Spec: [../retention.md](../retention.md)
Supersedes: [ADR-0010](0010-open-core-mit.md), [ADR-0011](0011-cost-model-quotas-retention.md)

## Context

ADR-0010 set Anvil up as open core on the Umami pattern: MIT-licensed and
uncapped when self-hosted, with revenue from a hosted Anvil Cloud running the
same codebase under `DEPLOYMENT_MODE=cloud`. ADR-0011 layered the cost model on
top — per-tier trace and eval-item quotas, monthly metering, and per-tier
retention windows.

Phase 10 was never built, so this exists almost entirely as specification: a
plan table, a metering design, quota enforcement at four creation endpoints, an
80%-of-quota banner, downgrade-freeze semantics, an admin plan-change API, and a
test suite required to run twice, once per mode.

The project owner has decided there will be no hosted offering and no
commercial tiers. Anvil is simply free software.

## Decision

**Anvil is MIT-licensed and complete. There are no plans, no quotas, no
metering for billing purposes, and no hosted tier.**

Phase 10 is rewritten rather than deleted, because one piece of it was never
about monetization: an operator needs a way to stop the database growing
forever. The phase becomes **Retention & housekeeping** — the batched retention
worker plus a usage panel with no quota bar — and `plan/pricing.md` becomes
`plan/retention.md`.

Dropped outright: plan definitions and `anvil_api/plans.py`, monthly Redis
usage counters, the `quota_exceeded` 429 at ingest, the 80% warning, plan
gating on projects/monitors/webhooks/eval runs, downgrade freeze semantics, the
`POST /v1/admin/orgs/{id}/plan` endpoint, and the both-modes test matrix.

### What is deliberately *not* dropped

- **Per-trace LLM cost.** The model price table, cost roll-ups and the overview
  cost chart tell an operator what their own model calls cost. That is the
  product, not billing.
- **Per-run eval spend guards** (`MAX_EVAL_ITEMS`, `MAX_CONCURRENT_RUNS`).
  These stop a mistyped loop from burning the operator's own provider credits.
  They were always footgun protection and were always env-tunable.
- **Rate limits.** Auth, per-key ingest and feedback limits are abuse and
  fairness controls on a multi-tenant service. Nothing to do with plans.

### `DEPLOYMENT_MODE` stays, with its meaning narrowed

The flag survives, but only for what it does in code today: gating third-party
plugin loading, so a deployment holding other people's data executes built-ins
only ([ADR-0012](0012-plugin-system.md)).

That concern outlives the business model. Anyone running a shared instance —
one company-wide install, a community server — should not execute arbitrary
third-party Python, whether or not money changes hands. So `cloud` no longer
means "our commercial offering"; it means "a shared, multi-tenant deployment".

The value is left named `cloud` rather than renamed to `shared`. A rename would
touch both service configs, compose, `.env.example` and two ADRs to change a
string that no deployment currently sets, and "cloud" still describes the
deployment shape accurately. Worth revisiting if the confusion outweighs the
churn.

### `organizations.plan` is left in place

The column stays, defaulting to `free`, with no meaning. `plan.md` establishes
that migration `0001` is the complete v1 schema and later phases should not
need migrations; a `0002` that drops a harmless unread column does not earn
that. The seed no longer needs `enterprise` to dodge limits, but setting it
costs nothing and is left alone. Treat the column as vestigial and do not read
it in new code.

## Consequences

- Phase 10 shrinks from the largest remaining spec to a small worker plus a
  read-only panel. The edge-case checklist loses rows 21, 22, 25 and 27
  entirely; rows 23 (batched deletes) and 24 (retention deleting a dataset's
  source trace) survive against the retention worker.
- The test suite no longer has to run in two deployment modes, which was the
  single largest testing burden the plan carried.
- No feature can be withheld to create an upgrade path, which removes a whole
  category of future design pressure.
- The MIT license is unchanged and was never the thing in question — what
  changes is that there is no longer a second, paid thing standing next to it.

## Alternatives rejected

**Keep the plan/quota machinery unbuilt but specified, in case of a hosted
offering later.** Rejected: unbuilt specification rots, and it was actively
misleading — it made an entire phase look like committed work. Reversing this
ADR is a fresh decision with fresh requirements, not a resumption of these
tables.

**Delete Phase 10 entirely.** Retention is not a pricing feature. Deleting the
phase would leave every operator with a database that grows without bound and
no supported way to prune it.
