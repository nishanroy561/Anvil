# ADR-0005: Trigram-indexed search projection

Status: Accepted · Date: 2026-07 · Spec: ../ingestion.md §4–5

## Context
Free-text search over trace input/output via bare `ILIKE` on JSONB seq-scans and dies around a few hundred thousand traces. Trace search is substring-shaped: error codes, ID fragments, prompt snippets — not prose relevance.

## Decision
A worker-built `search_text` column (lowercased name + root input/output text, capped 4 KB) with a `pg_trgm` GIN index; queries require a server-enforced time bound (default 7 d, max 30 d) and ≥3 characters. Span payloads stay unsearchable.

## Consequences
`ILIKE '%q%'` becomes an index scan; insert cost bounded by the 4 KB cap. No relevance ranking, no phrase semantics — fine for "find the trace containing X". Phase 11 verifies via `EXPLAIN` that the index is actually hit.

## Alternatives considered
- **Postgres FTS (tsvector)** — word-based + stemming; cannot substring-match error codes/IDs. Wrong semantics.
- **OpenSearch** — heavy second cluster + sync pipeline for what is a filter box, not a search product. Rejected at any foreseeable scale.
- **ClickHouse ngram indexes** — viable, but only alongside the analytics migration (ADR-0006), not for search alone.
