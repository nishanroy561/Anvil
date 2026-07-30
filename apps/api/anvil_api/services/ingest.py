"""Ingest write path: validate → normalize → XADD (plan/ingestion.md §1–2).

**No DB write happens on the request path.** The endpoint's only durable act is
appending to a Redis Stream; the worker owns Postgres. That is what lets ingest
absorb bursts far above database write throughput (ADR-0002).
"""

import json
import logging
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..metrics import INGEST_ACCEPTED, INGEST_REJECTED, TRUNCATED_FIELDS
from ..streams import STATS_KEY, TRACES_STREAM
from ..tracing import traceparent
from .normalize import normalize_trace

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "trace_schema.json"


@lru_cache
def _validator() -> Draft202012Validator:
    """Vendored copy of packages/shared/trace-schema.json (see ADR-0014 and
    scripts/sync_schemas.py); test_schema_sync.py guards it against drift."""
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate_batch(payload: Any) -> list[dict]:
    """Returns per-trace errors, most useful first. Empty list means valid.

    Errors carry the JSON path so an SDK author can find the offending field
    without diffing their payload against the schema by hand.
    """
    errors = []
    for error in sorted(_validator().iter_errors(payload), key=lambda e: list(e.path)):
        path = list(error.path)
        errors.append(
            {
                "path": "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in path),
                "trace_index": path[1] if len(path) > 1 and path[0] == "traces" else None,
                "message": error.message,
            }
        )
    return errors


async def stream_depth(redis: Redis) -> int:
    return int(await redis.xlen(TRACES_STREAM))


async def publish_traces(
    redis: Redis, traces: list[dict], project_id: uuid.UUID, max_field_bytes: int, maxlen: int
) -> int:
    """Normalizes and appends each trace to the stream. Returns count accepted.

    `project_id` is stamped from the resolved API key and overwrites anything
    the client sent — payload tenancy is never trusted (plan.md).
    """
    truncated_total = 0
    pipe = redis.pipeline(transaction=False)

    # The one place Anvil's own request context can cross into the worker. The
    # API's durable act is this XADD; the worker reads it later, on another
    # connection, in another process — so a distributed trace only spans the two
    # if the parent is written into the message (plan/observability.md §4).
    # None when OTel is off, which is the default, and then this is a no-op.
    parent = traceparent()

    for trace in traces:
        normalize_trace(trace, max_field_bytes)
        stats = trace.pop("_anvil_stats", {})
        truncated_total += stats.get("truncated_fields", 0)

        trace["project_id"] = str(project_id)
        fields = {"payload": json.dumps(trace, default=str)}
        if parent:
            # A separate stream field, not a key inside the payload: the payload is
            # validated against the trace wire schema, which forbids unknown
            # properties, and telemetry has no business in the customer's data.
            fields["traceparent"] = parent
        pipe.xadd(
            TRACES_STREAM,
            fields,
            maxlen=maxlen,
            approximate=True,
        )

    await pipe.execute()

    if truncated_total:
        TRUNCATED_FIELDS.inc(truncated_total)
    INGEST_ACCEPTED.labels(project=str(project_id)).inc(len(traces))
    return len(traces)


async def record_stat(redis: Redis, key_prefix: str, outcome: str) -> None:
    """Best-effort ingest counters for /v1/ingest/stats. Diagnostics must never
    fail the request that produced them."""
    try:
        await redis.hincrby(STATS_KEY, f"{key_prefix}:{outcome}", 1)
        await redis.hset(STATS_KEY, f"{key_prefix}:last_seen", str(int(time.time())))
    except RedisError:
        log.debug("ingest stat write failed (non-fatal)", exc_info=True)


def count_rejection(cause: str) -> None:
    INGEST_REJECTED.labels(cause=cause).inc()
