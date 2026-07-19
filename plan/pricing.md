# Pricing — Open-Core Model, Cloud Plans, Metering & Retention (Phase 10)

> Part of the [Anvil implementation plan](../plan.md). Schema (`organizations.plan`, `plan_overrides`, `org_usage`, FK cascades) in [architecture.md](architecture.md) §4. The quota check hooks into the ingest endpoint ([ingestion.md](ingestion.md) §2); eval-item quotas layer on the spend guards in [evaluation.md](evaluation.md) §3.

## 0. Business model: open-source core, monetize convenience (the Umami model)

Decision records: [ADR-0010](adr/0010-open-core-mit.md) (open core, MIT, mode flag) and [ADR-0011](adr/0011-cost-model-quotas-retention.md) (quota + retention cost model).

Anvil is **open source (MIT) and fully featured when self-hosted** — no artificial caps, no crippled features, no "community edition." Trust is built on that. Revenue comes from **Anvil Cloud**: the same codebase, hosted and operated by us, where customers pay for convenience — managed infra, zero-ops upgrades, backups, retention at scale, support — via Free/Pro/Enterprise tiers.

This means everything in this doc is governed by a single deployment flag:

- `DEPLOYMENT_MODE=self_hosted` (**the default** — a fresh `docker compose up` is a full, unlimited Anvil): plan/quota checks are no-ops; every feature (webhooks, unlimited projects/monitors/eval items) is enabled; retention is optional and env-configured (`RETENTION_DAYS`, unset = keep forever). The Plan & Usage UI shows usage stats but no quotas or upgrade CTAs.
- `DEPLOYMENT_MODE=cloud`: tiers, metering, quotas, and plan-based retention below all activate.

**Non-negotiable:** a self-hoster must never see `quota_exceeded`, a plan gate, or an upgrade nag on their own infrastructure. Every quota/gating code path checks the mode first, and tests cover both modes. One codebase, no `ee/` directory — the delta is configuration, not features.

## 1. Cloud plan definitions

In code: `anvil_api/plans.py` — static dict, env-overridable; Enterprise gets per-org overrides via `organizations.plan_overrides`.

| Limit | Self-hosted | Cloud Free | Cloud Pro | Cloud Enterprise |
|---|---|---|---|---|
| Traces / month | unlimited | 10k | 1M | custom |
| Retention | env-configured (∞ default) | 7 days | 90 days | 365 days / custom |
| Projects | unlimited | 1 | 10 | custom |
| Eval items / month (judge calls; incl. monitor scoring) | unlimited¹ | 200 | 20k | custom |
| Monitors | unlimited | 1 | 10 | custom |
| Alert webhooks | ✓ | – | ✓ | ✓ |
| Price | $0 forever | $0 | ~$49/org/mo (placeholder) | custom |

¹ Self-hosters bring their own judge API keys, so their eval spend is already their own — the per-run `MAX_EVAL_ITEMS`/`MAX_CONCURRENT_RUNS` sanity guards ([evaluation.md](evaluation.md) §3) still apply in both modes as footgun protection, and remain env-tunable.

Prices are product decisions — placeholders in docs, never hardcoded in logic; only limits live in code.

## 2. Metering (cloud mode; self-hosted skips quota checks but still records usage for the stats page)

- Monthly Redis counters `usage:{org_id}:{YYYYMM}:traces` / `:eval_items`, incremented at ingest-accept and grading (including monitor scoring).
- Cloud mode: ingest checks the counter before `XADD`; over quota → `429 {"error": {"code": "quota_exceeded"}}` — the third 429 cause alongside backpressure and rate limit; `cause="quota"` on the rejected-counter metric ([observability.md](observability.md)).
- At 80% of quota: warning flag → dashboard banner + alert webhook, firing once per month per org.
- **Nightly reconciliation job:** recompute true counts from Postgres, overwrite the Redis counters (increments are best-effort and drift), upsert `org_usage` rollups for history. Runs in both modes (the stats are useful self-hosted too).

## 3. Enforcement at creation endpoints (cloud mode only)

Projects, monitors, alert-webhook config (Free: none), and eval runs — the monthly eval-item quota layers on top of the per-run spend guards.

## 4. Retention worker (`retention.py`, daily — both modes)

- Cloud: per org, window from the plan. Self-hosted: single global `RETENTION_DAYS` env (unset → worker idles).
- Delete traces older than the window in batches (5k per transaction, brief sleep between — never one giant DELETE that bloats WAL and holds locks).
- Spans/feedback/monitor_scores follow via `ON DELETE CASCADE`; `dataset_items.source_trace_id` is `ON DELETE SET NULL` so curated datasets survive retention (items copy their input/output).
- Code comment: at real scale, monthly declarative partitions make expiry a partition drop — explicitly not built in v1 (architecture.md §8).

## 5. UI & plan changes

Settings → **Plan & Usage**: usage stats (traces, eval items) in both modes. Cloud additionally: plan badge, usage-vs-quota bars, retention window, days-until-reset, upgrade CTA. Self-hosted shows a quiet "Self-hosted — unlimited. Anvil Cloud handles hosting for you →" link instead of quota UI (the only acceptable nudge). v1 cloud plan changes are an **admin action**: `POST /v1/admin/orgs/{id}/plan` guarded by `ADMIN_API_KEY` — no payment processing (out of scope, architecture.md §8).

## 6. Downgrade semantics (cloud)

Existing over-limit resources are **frozen, not deleted** (no new creation past the cap). The shorter retention window applies on the next sweep — destructive, so the UI must warn explicitly before an admin downgrade.

## 7. Tests & acceptance

Tests (run the suite in both modes): cloud — quota boundary (trace N accepted, N+1 → 429 `quota_exceeded`); 80% warning fires exactly once; reconciliation corrects a drifted counter; retention deletes only out-of-window traces of the right org, sets `source_trace_id` null, dataset item still evaluates; downgrade freezes creation without deleting. Self-hosted — the same over-quota ingest is **accepted**; webhook config allowed; project #2 creation allowed; retention idles with `RETENTION_DAYS` unset and works when set.

**Acceptance:** in cloud mode, a Free org seeded to 95% → banner, past quota → 429 + metric; flip the same stack to `self_hosted` → identical traffic ingests without limits and no quota UI appears; retention with a synthetic 1-day window works in both modes; datasets intact. (Demo org still seeds as `enterprise` so cloud-mode demos never trip limits.)
