# ADR-0003: Client-generated IDs + guarded upserts

Status: Accepted · Date: 2026-07 · Spec: ../ingestion.md §3

## Context
At-least-once delivery (ADR-0002) plus SDK retries means duplicates are normal. Trace IDs must be known client-side (feedback round-trips them). But client-supplied IDs open two attacks/races: a client writing another tenant's trace ID, and out-of-order in_progress/completed events across consumer-group workers.

## Decision
Trace/span IDs are client-generated UUIDs; persistence is `INSERT … ON CONFLICT DO UPDATE` with two mandatory guards: **tenant guard** (`WHERE project_id = excluded.project_id`) and **monotonic completion guard** (never regress a completed trace to `in_progress`).

## Consequences
Retries and redeliveries are free; the guards make the upsert safe against both cross-tenant overwrite and event reordering. Cost: the guards must appear on *every* write path that touches these tables — enforced by tests (checklist rows 1–2).

## Alternatives considered
- **Server-generated IDs** — kills idempotency and feedback round-tripping.
- **Dedup via Redis seen-set** — extra state, still racy across workers; the DB constraint is the only authoritative place.
