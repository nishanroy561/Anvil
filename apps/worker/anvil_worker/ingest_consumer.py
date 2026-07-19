"""Phase 2 — NOT IMPLEMENTED YET. Spec: plan/ingestion.md §3–4.

Must implement, in this order:
1. Consumer group `persisters` on stream `ingest:traces` (XREADGROUP,
   batch <=500 msgs / 200 ms), plus the `ingest:feedback` stream (Phase 9).
2. Guarded upserts (ADR-0003): tenant guard
   (`WHERE traces.project_id = excluded.project_id`) + monotonic completion
   guard (never regress a completed trace to in_progress). Spans: DO NOTHING.
3. XACK + XDEL after persist; XAUTOCLAIM (idle > 60 s) at startup + periodic.
4. DLQ: delivery count >= 5 (XPENDING) -> XADD ingest:dlq, XACK original.
5. Stale-trace reaper: in_progress > 30 min -> status 'abandoned' (every 5 min).
6. Derived fields at persist: cost (pricing.py) + search_text (4 KB cap).
7. Prometheus (port 9101): anvil_consumer_lag, anvil_persist_batch_seconds,
   anvil_traces_persisted_total, anvil_tenant_guard_blocks_total.
   NOTE: stream/DLQ depth gauges are exported by the API, not here (ADR-0009).
"""
