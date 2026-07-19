# ADR-0006: ClickHouse as the designated analytics scale path

Status: Accepted (deferred to v2 — do not build in v1) · Date: 2026-07 · Spec: ../architecture.md §8

## Context
Metrics overview and drift charts aggregate raw trace rows (`date_trunc`). At hundreds of millions of rows this — not search — is what falls over first. The products in this exact space (Langfuse v3, PostHog, SigNoz) all landed on Postgres + ClickHouse.

## Decision
When raw-row aggregates become the bottleneck, add ClickHouse alongside Postgres for trace analytics (and absorb search-at-scale via its ngram skip indexes). Until then: v1 stays Postgres-only; interim relief if needed is rollup tables, not a second store.

## Consequences
The v2 direction is settled — no OpenSearch detours, no premature dual-store complexity (outbox/sync, two consistency models, two backup stories) in v1. Trigger to watch: metrics/overview endpoint latency growth in Grafana.

## Alternatives considered
- **OpenSearch** — search engine, not OLAP; solves the problem we don't have.
- **Postgres partitioned rollups forever** — workable longer than people think, but high-cardinality slice-and-dice eventually needs columnar.
