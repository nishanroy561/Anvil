"""Feedback write path: validate → XADD (plan/evaluation.md §7).

Same shape as the trace write path and for the same reason — no DB write on the
request path — but here the asynchrony buys something extra. **A thumbs-down can
overtake the trace it points at**: the trace is still sitting in the Redis buffer
when the user clicks, so a synchronous insert would hit a foreign-key violation
and the customer would lose the single most valuable signal they can send us
(checklist row 12). Queueing it lets the worker wait for the trace to land.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis

from ..metrics import FEEDBACK_ACCEPTED, FEEDBACK_REJECTED
from ..streams import FEEDBACK_STREAM

log = logging.getLogger(__name__)


async def publish_feedback(
    redis: Redis,
    feedback_id: uuid.UUID,
    trace_id: uuid.UUID,
    project_id: uuid.UUID,
    rating: int,
    comment: str | None,
    user_ref: str | None,
    maxlen: int,
) -> None:
    """Append one feedback event to the stream.

    `enqueued_at` is stamped here, not in the worker: it starts the retry window
    that eventually dead-letters feedback whose trace never arrives. Stamping it
    on first *consumption* instead would restart the clock on every redelivery,
    and a row that can never persist would retry forever.
    """
    event = {
        "id": str(feedback_id),
        "project_id": str(project_id),
        "trace_id": str(trace_id),
        "rating": rating,
        "comment": comment,
        "user_ref": user_ref,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.xadd(
        FEEDBACK_STREAM,
        {"payload": json.dumps(event)},
        maxlen=maxlen,
        approximate=True,
    )
    FEEDBACK_ACCEPTED.labels(project=str(project_id)).inc()


def count_rejection(cause: str) -> None:
    FEEDBACK_REJECTED.labels(cause=cause).inc()
