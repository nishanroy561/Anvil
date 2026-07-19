# ADR-0010: Open core — MIT license, `DEPLOYMENT_MODE` flag, no `ee/` fork

Status: Accepted · Date: 2026-07 · Spec: ../pricing.md §0

## Context
Positioning follows the Umami pattern: build trust with an open-source core, monetize convenience via a hosted cloud. The trust only materializes if self-hosted Anvil is genuinely complete — feature-gated "community editions" read as bait.

## Decision
MIT license. Fully featured self-hosted (the default). One codebase; cloud behavior (tiers, quotas, plan retention) activates behind `DEPLOYMENT_MODE=cloud`. No `ee/` directory. Hard rule: a self-hoster never sees a quota, plan gate, or upgrade nag; the test suite runs in both modes.

## Consequences
Adoption path is frictionless; the moat is hosted ops + convenience, not code secrecy. MIT permits competitors hosting Anvil — accepted trade-off at this stage (AGPL's reseller protection isn't worth the adoption friction yet; relicensing later is a new ADR). Every future gated feature must route through the mode flag (checklist row 27).

## Alternatives considered
- **AGPL** — reseller protection at the cost of enterprise-adoption hesitancy; premature for a project whose current problem is obscurity, not resellers.
- **Open-core with `ee/` feature gating** (PostHog-style) — viable at scale, but splits the codebase and erodes the "fully yours self-hosted" trust pitch that is the point.
- **Closed SaaS** — no trust story against incumbent observability vendors.
