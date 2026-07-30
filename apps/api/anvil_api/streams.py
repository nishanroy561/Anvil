"""Redis Stream names and consumer-group identity (ADR-0002).

Shared vocabulary between the API (producer, backlog gauges) and the worker
(consumer). The worker declares these independently — see ADR-0014 — so any
change here must be mirrored in `anvil_worker/streams.py`.
"""

TRACES_STREAM = "ingest:traces"
FEEDBACK_STREAM = "ingest:feedback"
DLQ_STREAM = "ingest:dlq"

# Eval job queue (plan/evaluation.md §3). Same Redis, separate stream and group
# so a backed-up eval queue can never delay trace ingestion.
EVAL_JOBS_STREAM = "eval:jobs"

PERSISTERS_GROUP = "persisters"
EVAL_RUNNERS_GROUP = "eval-runners"
# Feedback gets its own group (Phase 9): its consumer defers rows whose trace
# hasn't landed yet, and that retry behaviour must not hold up trace persistence.
FEEDBACK_GROUP = "feedback-persisters"

# Redis hash of ingest counters, exposed at /v1/ingest/stats so a throttled
# customer is diagnosable (plan/ingestion.md §2).
STATS_KEY = "ingest:stats"
