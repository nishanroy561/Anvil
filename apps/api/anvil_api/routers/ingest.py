"""Ingest endpoint (plan/ingestion.md §1–2).

Ordering here is deliberate: the cheap per-key request limit runs before JSON
parsing so a client in a tight loop can't make us parse 5 MB to reject it,
while the trace-count limit necessarily runs after parsing. Global backpressure
is checked last, immediately before the write.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError

from ..auth import ApiKeyContext, current_api_key_context
from ..config import get_settings
from ..redis_client import get_redis
from ..services.ingest import (
    count_rejection,
    publish_traces,
    record_stat,
    stream_depth,
    validate_batch,
)
from ..services.rate_limit import enforce_rate_limit
from ..streams import STATS_KEY

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])
log = logging.getLogger(__name__)


def _reject(status: int, code: str, message: str, **extra) -> HTTPException:
    count_rejection(code)
    return HTTPException(status, detail={"code": code, "message": message, **extra})


@router.post("/traces", status_code=202)
async def ingest_traces(
    request: Request, context: ApiKeyContext = Depends(current_api_key_context)
):
    settings = get_settings()
    redis = get_redis()
    key_id = str(context.api_key.id)
    project_id = context.project.id

    # Per-key request limit — the noisy-neighbor guard. Separate from global
    # backpressure, which would otherwise punish innocent tenants for one
    # client's tight loop (checklist row 16).
    try:
        await enforce_rate_limit(
            redis, "ingest:requests", key_id, settings.ingest_rate_limit_rpm
        )
    except HTTPException:
        count_rejection("rate_limited")
        await record_stat(redis, key_id, "rate_limited")
        raise

    body = await request.body()
    if len(body) > settings.ingest_max_body_bytes:
        await record_stat(redis, key_id, "too_large")
        raise _reject(
            413,
            "payload_too_large",
            f"Body exceeds {settings.ingest_max_body_bytes} bytes; send smaller batches",
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _reject(400, "invalid_json", f"Body is not valid JSON: {exc.msg}")

    if errors := validate_batch(payload):
        await record_stat(redis, key_id, "invalid")
        # Cap the error list: a batch of 500 malformed traces shouldn't return
        # a response larger than the request.
        raise _reject(
            422, "invalid_payload", "Batch does not match the trace wire format",
            errors=errors[:20], error_count=len(errors),
        )

    traces = payload["traces"]

    try:
        await enforce_rate_limit(
            redis,
            "ingest:traces",
            key_id,
            settings.ingest_rate_limit_traces_pm,
            amount=len(traces),
        )
    except HTTPException:
        count_rejection("rate_limited")
        await record_stat(redis, key_id, "rate_limited")
        raise

    # Global backpressure — protects Anvil itself. Checked last so we don't
    # spend a queue slot on a batch that was going to fail validation.
    try:
        depth = await stream_depth(redis)
    except RedisError:
        log.error("redis unavailable at ingest; returning 503 so SDKs retry")
        raise _reject(503, "buffer_unavailable", "Ingestion buffer unavailable; retry shortly")

    if depth > settings.ingest_high_water:
        await record_stat(redis, key_id, "backpressure")
        raise HTTPException(
            429,
            detail={
                "code": "backpressure",
                "message": "Ingestion buffer is saturated; retry shortly",
            },
            headers={"Retry-After": "5"},
        )

    try:
        accepted = await publish_traces(
            redis,
            traces,
            project_id,
            settings.ingest_max_field_bytes,
            settings.ingest_stream_maxlen,
        )
    except RedisError:
        log.error("redis XADD failed at ingest; returning 503 so SDKs retry")
        raise _reject(503, "buffer_unavailable", "Ingestion buffer unavailable; retry shortly")

    await record_stat(redis, key_id, "accepted")
    return {"accepted": accepted}


@router.get("/stats")
async def ingest_stats(context: ApiKeyContext = Depends(current_api_key_context)):
    """Per-key accept/reject counters plus queue depth, so a throttled customer
    can see *why* without reading Anvil's logs (plan/ingestion.md §2)."""
    redis = get_redis()
    key_id = str(context.api_key.id)
    try:
        raw = await redis.hgetall(STATS_KEY)
        depth = await stream_depth(redis)
    except RedisError:
        raise HTTPException(
            503, detail={"code": "stats_unavailable", "message": "Redis unavailable"}
        )

    prefix = f"{key_id}:"
    counters = {
        k[len(prefix):]: (int(v) if v.isdigit() else v)
        for k, v in raw.items()
        if k.startswith(prefix)
    }
    return {
        "key_prefix": context.api_key.key_prefix,
        "counters": counters,
        "stream_depth": depth,
        "high_water": get_settings().ingest_high_water,
    }
