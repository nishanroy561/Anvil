"""Stream names and group identity, mirrored from `anvil_api/streams.py`.

Duplicated deliberately: the worker takes no dependency on the API package
(ADR-0014). These names are wire protocol between the two services — change
them in both places, in the same commit.
"""

TRACES_STREAM = "ingest:traces"
FEEDBACK_STREAM = "ingest:feedback"
DLQ_STREAM = "ingest:dlq"
EVAL_JOBS_STREAM = "eval:jobs"

PERSISTERS_GROUP = "persisters"
EVAL_RUNNERS_GROUP = "eval-runners"
FEEDBACK_GROUP = "feedback-persisters"

# Sorted set of feedback rows waiting for their trace to land, scored by the
# time of their next attempt (Phase 9, checklist row 12). A Redis key rather
# than a stream because a stream has no way to say "not before time T" — the
# consumer would have to spin through undue entries on every read.
FEEDBACK_DEFERRED_KEY = "feedback:deferred"
