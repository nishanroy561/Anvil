"""Detect a running worker, for the acceptances that must not have one.

`feedback_acceptance.py` and `drift_alert_acceptance.py` drive the worker's loops
**in-process**, a cycle at a time, because what they test is timing: a vote that
overtakes its trace, an alert that fires "within one cycle". Sleeping through real
backoffs would add flakiness, not strength.

A live worker competes for exactly the same work. It reads the same consumer
group and claims the same monitors, so the script's in-process consumer finds the
queue already drained. The visible symptom is a bare `wrote 0` several steps into
the run — which reads as "persistence is broken" and sends you debugging the
wrong thing. It cost a debugging cycle to recognise, which is why this exists.

The check is deliberately advisory in shape but fatal in effect: it is better to
refuse to start than to produce a red result that means nothing.
"""

from __future__ import annotations

# A consumer idle for longer than this is a dead process's leftover entry, not a
# competitor. Entries persist in XINFO CONSUMERS until something reclaims them,
# so idle time is the only thing separating "running" from "crashed hours ago".
LIVE_IDLE_MS = 60_000


async def live_consumers(redis, stream: str, group: str, ours: set[str]) -> list[str]:
    """Names of consumers in `group` that are active and not ours."""
    try:
        consumers = await redis.xinfo_consumers(stream, group)
    except Exception:  # noqa: BLE001 - no stream or no group means nobody is running
        return []
    return [
        c["name"]
        for c in consumers
        if c["name"] not in ours and int(c.get("idle", 0)) < LIVE_IDLE_MS
    ]


async def refuse_if_worker_running(redis, stream: str, group: str, ours: set[str]) -> str | None:
    """Returns an error message if a worker is competing, else None."""
    live = await live_consumers(redis, stream, group, ours)
    if not live:
        return None
    return (
        f"a worker is already consuming {stream!r} (consumers: {', '.join(live)}).\n"
        "This acceptance drives the consumer loops in-process, so a running worker\n"
        "takes the messages first and the checks fail with counts of zero that look\n"
        "like a broken write path.\n\n"
        "Stop it first:\n"
        "    docker compose stop worker        # compose\n"
        "    # or kill the `python -m anvil_worker.main` process"
    )
