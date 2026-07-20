"""Prometheus series owned by the API (plan/observability.md §1).

The backlog gauges (stream/DLQ depth) live HERE, not in the worker: a dead
worker can't report the backlog it isn't draining (ADR-0009). They're
refreshed by a background task started in main.py's lifespan."""

from prometheus_client import Counter, Gauge

INGEST_ACCEPTED = Counter(
    "anvil_ingest_traces_accepted_total", "Traces accepted for ingestion", ["project"]
)
INGEST_REJECTED = Counter(
    "anvil_ingest_rejected_total", "Ingest requests rejected", ["cause"]
)
TRUNCATED_FIELDS = Counter(
    "anvil_ingest_truncated_fields_total", "Oversized payload fields truncated at ingest"
)
RATELIMIT_REJECTIONS = Counter(
    "anvil_ratelimit_rejections_total", "Rate-limit rejections", ["scope"]
)
STREAM_DEPTH = Gauge("anvil_stream_depth", "XLEN of ingest:traces")
DLQ_DEPTH = Gauge("anvil_dlq_depth", "XLEN of ingest:dlq")
