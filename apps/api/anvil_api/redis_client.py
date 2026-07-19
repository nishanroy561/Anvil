import redis.asyncio as redis

from .config import get_settings

_pool: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _pool
