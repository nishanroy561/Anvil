"""Redis Stream names and consumer-group identity (ADR-0002).

Shared vocabulary between the API (producer, backlog gauges) and the worker
(consumer). The worker declares these independently — see ADR-0014 — so any
change here must be mirrored in `anvil_worker/streams.py`.
"""

TRACES_STREAM = "ingest:traces"
FEEDBACK_STREAM = "ingest:feedback"
DLQ_STREAM = "ingest:dlq"

PERSISTERS_GROUP = "persisters"

# Redis hash of ingest counters, exposed at /v1/ingest/stats so a throttled
# customer is diagnosable (plan/ingestion.md §2).
STATS_KEY = "ingest:stats"
