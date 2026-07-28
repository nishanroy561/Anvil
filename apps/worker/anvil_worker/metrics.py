"""Prometheus series owned by the worker (plan/observability.md §1).

Queue *depth* is deliberately absent: the API exports it, because a dead worker
cannot report the backlog it is failing to drain (ADR-0009). What lives here is
what only a running worker knows — throughput, batch latency, and how often the
safety guards actually fire.
"""

from prometheus_client import Counter, Gauge, Histogram

TRACES_PERSISTED = Counter(
    "anvil_traces_persisted_total", "Traces written to Postgres"
)
SPANS_PERSISTED = Counter("anvil_spans_persisted_total", "Spans written to Postgres")
PERSIST_BATCH_SECONDS = Histogram(
    "anvil_persist_batch_seconds", "Wall time to persist one batch"
)
CONSUMER_LAG = Gauge(
    "anvil_consumer_lag", "Entries pending for the persisters group"
)
TENANT_GUARD_BLOCKS = Counter(
    "anvil_tenant_guard_blocks_total",
    "Upserts refused because the row belongs to another project",
    ["project"],
)
DLQ_MESSAGES = Counter(
    "anvil_dlq_messages_total", "Messages routed to the dead-letter queue"
)
RECLAIMED_MESSAGES = Counter(
    "anvil_reclaimed_messages_total", "Pending entries reclaimed from a dead consumer"
)
ABANDONED_TRACES = Counter(
    "anvil_abandoned_traces_total", "Stale in_progress traces marked abandoned"
)

# Eval runner (Phase 5). Item throughput is the useful signal — a run that
# stalls mid-batch shows up as a flat counter with the run still `running`.
EVAL_ITEMS = Counter("anvil_eval_items_total", "Dataset items scored")
EVAL_RUNS = Counter("anvil_eval_runs_total", "Eval runs reaching a terminal state", ["status"])

# ---- Phase 8: monitors & alerts ---------------------------------------------

MONITOR_SAMPLES = Counter(
    "anvil_monitor_samples_total", "Live traces sampled and scored by monitors"
)
ALERTS_FIRED = Counter("anvil_alerts_fired_total", "Alert events opened", ["metric"])
ALERTS_RESOLVED = Counter("anvil_alerts_resolved_total", "Alert events resolved", ["metric"])
ALERT_DELIVERY_FAILURES = Counter(
    "anvil_alert_delivery_failures_total",
    "Alerts that fired but could not be delivered to every channel",
)
