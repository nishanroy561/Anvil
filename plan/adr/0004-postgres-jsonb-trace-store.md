# ADR-0004: Postgres JSONB trace store; span payloads unindexed

Status: Accepted · Date: 2026-07 · Spec: ../architecture.md §4

## Context
Span payloads are heterogeneous (llm/retrieval/tool/… each with different attributes) and schema evolves with the ecosystem. Write volume is high; the README fixes Postgres.

## Decision
Typed columns for everything queried/filtered/aggregated (timestamps, status, tokens, cost, model, project_id); JSONB for payloads (`input`/`output`/`attributes`). GIN only on `traces.metadata`; **span payloads get no index** — they're fetched by `trace_id`, never searched. Payload fields capped at 100 KB (truncation with marker).

## Consequences
Flexible payloads without migrations per span type; hot queries never touch JSONB. GIN write amplification avoided on the highest-volume table. Free-text needs came back later and were solved by a dedicated projection (ADR-0005), not by indexing payloads.

## Alternatives considered
- **Fully normalized span-attribute tables** — migration churn for every new attribute; joins on the hottest read path.
- **Document DB for spans** — second datastore, and loses transactional writes with the relational tenancy model.
