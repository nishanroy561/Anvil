# ADR-0002: Redis Streams for ingestion buffer and job queue

Status: Accepted · Date: 2026-07 · Spec: ../ingestion.md, ../evaluation.md §3

## Context
Ingestion must be async (no DB writes on the request path) with backpressure and durability; eval batches need a job queue with crash recovery. Redis is already in the fixed stack.

## Decision
Redis Streams with consumer groups for both: `ingest:traces` / `ingest:feedback` (buffer) and `eval:jobs` (queue). `XAUTOCLAIM` for crash reclaim, delivery-count → DLQ for poison messages, `MAXLEN ~` + high-water 429 for backpressure, AOF persistence for durability.

## Consequences
One infrastructure primitive serves both needs; consumer groups give horizontal workers for free; at-least-once delivery means the persistence layer must be idempotent (ADR-0003). Redis AOF durability is weaker than Kafka's replicated log — acceptable at v1 scale, revisit if a lost fsync window ever matters.

## Alternatives considered
- **Kafka** — better durability/replay, but a heavy second stateful system for a v1 that Redis already covers.
- **Celery/RQ** — job semantics only; doesn't solve the ingestion buffer, and hides the queue mechanics we need to control (backpressure, DLQ).
- **Postgres queue (SKIP LOCKED)** — puts the burst load on the exact database we're buffering to protect.
