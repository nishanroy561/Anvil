"""Fixed-window Redis rate limiter (ADR-0007). Fail-open: a rate limiter
must never become the outage. ~2x burst at window edges is accepted."""

import logging
import time

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


async def enforce_rate_limit(
    redis: Redis, scope: str, identifier: str, limit: int, window_s: int = 60
) -> None:
    """Raise 429 if `identifier` exceeded `limit` calls in the current window.
    Redis failure -> allow (fail-open)."""
    window = int(time.time() // window_s)
    key = f"rl:{scope}:{identifier}:{window}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_s)
    except RedisError:
        log.warning("rate limiter fail-open (redis unavailable) scope=%s", scope)
        return
    if count > limit:
        retry_after = window_s - int(time.time() % window_s)
        raise HTTPException(
            429,
            detail={"code": "rate_limited", "message": f"Rate limit exceeded for {scope}"},
            headers={"Retry-After": str(retry_after), "X-RateLimit-Remaining": "0"},
        )
