"""Phase 10 — NOT IMPLEMENTED YET. Spec: plan/pricing.md §4.

Daily: delete traces older than the window (cloud: per-org plan window;
self-hosted: RETENTION_DAYS env, unset -> idle) in batches of 5k/txn with
sleeps. Spans/feedback/monitor_scores cascade; dataset_items.source_trace_id
is SET NULL. Never one giant DELETE."""
