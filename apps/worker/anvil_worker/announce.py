"""Publishes the worker's live grader registry for the API to read (ADR-0023).

Graders are discovered by entry point in the *worker*, but the picker is served by
the *API*, which cannot import `apps/worker` (ADR-0014). Without this, installing a
plugin gave an operator a grader the worker could run and the UI would not offer.

Redis is already the bus between these two services, so this adds a message rather
than a dependency. Three properties make it safe to be wrong about:

- **TTL'd.** Remove a plugin and restart, and the stale entry expires by itself.
  Stop the worker and the picker falls back to built-ins rather than advertising
  graders nothing can run.
- **Advisory.** The API uses the snapshot to *offer* graders, never to reject a
  request. A buggy plugin cannot lock its own users out of a form.
- **Fail-soft.** Every failure here is logged and swallowed. A worker that cannot
  announce itself still grades; it just isn't listed.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from redis.asyncio import Redis

from .graders import plugin_snapshot

log = logging.getLogger(__name__)

REGISTRY_KEY = "anvil:registry:graders"

# Refresh well inside the TTL so a single missed cycle never blanks the picker.
REFRESH_S = 300
TTL_S = 900


async def publish(redis: Redis) -> int:
    """Write the current snapshot. Returns how many plugin graders were announced."""
    entries = plugin_snapshot()
    payload = json.dumps(
        {"published_at": datetime.now(timezone.utc).isoformat(), "graders": entries}
    )
    await redis.set(REGISTRY_KEY, payload, ex=TTL_S)
    return len(entries)


class Announcer:
    """Republishes on a timer. Owns no state beyond a stop flag."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> int:
        try:
            count = await publish(self.redis)
        except Exception:  # noqa: BLE001 - a failed announce costs a listing, never the worker
            log.warning("could not announce the grader registry", exc_info=True)
            return 0
        if count:
            log.info("announced %d plugin grader(s) to the API", count)
        return count

    async def run(self) -> None:
        while not self._stop:
            await self.tick()
            slept = 0.0
            while slept < REFRESH_S and not self._stop:
                await asyncio.sleep(min(5.0, REFRESH_S - slept))
                slept += 5.0
