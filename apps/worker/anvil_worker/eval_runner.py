"""Phase 5 — NOT IMPLEMENTED YET. Spec: plan/evaluation.md §3.

Key requirements: `eval:jobs` consumer group; heartbeat eval_runs.updated_at
every ~15 s; resume stale runs via unique (run_id, item_id); item snapshot in
eval_runs.item_ids; provider-429 backoff; grader errors excluded from
aggregates. Graders load via the plugin registry (plan/plugins.md).
Prometheus on port 9102."""
