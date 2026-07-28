"""Live-traffic sampling and scoring (plan/evaluation.md §6).

Every 30 s, each enabled monitor whose cron is due samples recent unscored
traces and grades their root input/output through the same grader machinery
eval runs use. Results land in `monitor_scores`, which is what the drift chart
and the `eval_score` alert metric read.

Three things here are load-bearing:

- **A trace is scored at most once per monitor.** The candidate query
  anti-joins `monitor_scores`, so a monitor that runs every 10 minutes over a
  2-hour lookback does not re-grade — and re-charge for — the same traffic each
  cycle. Sampling live traffic is the one part of the product that spends judge
  credits without a human asking it to.
- **`last_run_at` is claimed before any grading.** Two workers must not both
  decide the same monitor is due; the claim is a conditional UPDATE, so the
  loser matches zero rows and skips.
- **Cron is evaluated in UTC** (checklist row 14). A monitor drifting by an
  hour twice a year is the kind of bug nobody finds for months.
"""

import asyncio
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update

from .db import transaction
from .graders import GradeContext, run_grader
from .metrics import MONITOR_SAMPLES
from .tables import monitor_configs, monitor_scores, traces

log = logging.getLogger(__name__)

# How often the loop wakes to look for due monitors. Not the sampling cadence —
# that is each monitor's own cron.
TICK_S = 30

# Ceiling on traces graded per monitor per cycle, whatever the sample rate says.
# A traffic spike must not turn into a judge bill — the same instinct as the
# eval spend guards, applied to the one path a human did not explicitly trigger.
MAX_PER_RUN = 50

# How far back a cycle looks for candidates. Wider than any reasonable cron so a
# monitor that was disabled, or a worker that was down, still catches up — but
# bounded, so the anti-join stays cheap.
LOOKBACK_MINUTES = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_due(cron: str, last_run_at: datetime | None, now: datetime | None = None) -> bool:
    """Whether `cron` has come due since `last_run_at`, evaluated in UTC.

    A monitor that has never run is due immediately — otherwise enabling one
    means waiting a full cron interval to find out whether it works.
    """
    now = now or _now()
    if last_run_at is None:
        return True
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=timezone.utc)

    try:
        from croniter import croniter
    except ImportError:
        # Without croniter the schedule cannot be honoured. Defaulting to
        # "always due" would silently turn a daily monitor into a 30-second one
        # and spend real money, so the safe default is the other way.
        log.warning("croniter not installed; monitor schedules cannot be evaluated")
        return False

    try:
        return croniter(cron, last_run_at).get_next(datetime) <= now
    except Exception:
        log.exception("invalid cron %r; treating monitor as not due", cron)
        return False


async def due_monitors(now: datetime | None = None) -> list[dict]:
    now = now or _now()
    async with transaction() as conn:
        rows = await conn.execute(
            select(monitor_configs).where(monitor_configs.c.enabled.is_(True))
        )
        monitors = [dict(r) for r in rows.mappings()]
    return [m for m in monitors if is_due(m["schedule_cron"], m["last_run_at"], now)]


async def claim(monitor_id: uuid.UUID, previous: datetime | None) -> bool:
    """Marks the monitor run, conditional on `last_run_at` not having moved.

    This is the whole concurrency story: two workers ticking in the same second
    both see the monitor as due, both try to claim, and exactly one UPDATE
    matches. The loser grades nothing rather than double-charging.
    """
    async with transaction() as conn:
        condition = (
            monitor_configs.c.last_run_at.is_(None)
            if previous is None
            else monitor_configs.c.last_run_at == previous
        )
        result = await conn.execute(
            update(monitor_configs)
            .where(and_(monitor_configs.c.id == monitor_id, condition))
            .values(last_run_at=_now())
        )
        return result.rowcount > 0


async def candidates(monitor: dict, limit: int = MAX_PER_RUN * 4) -> list[dict]:
    """Recent traces in this project that this monitor has not already scored."""
    since = _now() - timedelta(minutes=LOOKBACK_MINUTES)
    already = select(monitor_scores.c.trace_id).where(
        monitor_scores.c.monitor_id == monitor["id"]
    )
    async with transaction() as conn:
        rows = await conn.execute(
            select(traces)
            .where(
                traces.c.project_id == monitor["project_id"],
                traces.c.started_at >= since,
                # Finished traces only: an in-progress one has no output yet,
                # and grading it would score an empty string as a failure.
                traces.c.status.in_(("ok", "error")),
                traces.c.id.notin_(already),
            )
            .order_by(traces.c.started_at.desc())
            .limit(limit)
        )
        return [dict(r) for r in rows.mappings()]


def sample(rows: list[dict], rate: float, cap: int = MAX_PER_RUN) -> list[dict]:
    """Bernoulli selection at `rate`, capped.

    Per-trace rather than "take the first N": taking the head of a
    time-ordered list would systematically sample one slice of the hour and
    then call it representative.
    """
    rate = max(0.0, min(1.0, float(rate)))
    picked = list(rows) if rate >= 1.0 else [r for r in rows if random.random() < rate]
    return picked[:cap]


async def score_trace(monitor: dict, trace: dict, model_factory=None) -> dict:
    """Grades one trace's root input/output. Never raises."""
    graders = (monitor.get("grader_config") or {}).get("graders", [])
    context = GradeContext(
        input=trace.get("input"),
        # Live traffic has no reference answer — nothing was curated. Graders
        # that score against `expected_output` therefore cannot work here and
        # record their own error rather than a misleading zero.
        expected_output=None,
        metadata=trace.get("metadata"),
        model_factory=model_factory,
    )
    scores: dict = {}
    for spec in graders:
        name = spec.get("name", "")
        result, error = await run_grader(
            name, trace.get("output"), context, spec.get("config") or {}
        )
        scores[name] = (
            {"error": error}
            if error is not None
            else {"score": result.score, "detail": result.detail}
        )
    return {
        "id": uuid.uuid4(),
        "monitor_id": monitor["id"],
        "trace_id": trace["id"],
        "scores": scores or None,
        "created_at": _now(),
    }


async def run_monitor(monitor: dict, model_factory=None) -> int:
    """One cycle for one monitor. Returns how many traces it scored."""
    rows = await candidates(monitor)
    picked = sample(rows, monitor.get("sample_rate") or 0.0)
    if not picked:
        return 0

    scored = [await score_trace(monitor, trace, model_factory) for trace in picked]
    async with transaction() as conn:
        await conn.execute(monitor_scores.insert(), scored)
    MONITOR_SAMPLES.inc(len(scored))
    log.info("monitor %s scored %d of %d candidates", monitor["name"], len(scored), len(rows))
    return len(scored)


class Sampler:
    """The 30-second loop. Owns no state beyond a stop flag."""

    def __init__(self, model_factory=None) -> None:
        self.model_factory = model_factory  # tests inject fake LLMs here
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> int:
        total = 0
        for monitor in await due_monitors():
            if not await claim(monitor["id"], monitor["last_run_at"]):
                continue  # another worker got it
            try:
                total += await run_monitor(monitor, self.model_factory)
            except Exception:
                # One bad monitor must not stop the others, or the next cycle.
                log.exception("monitor %s failed", monitor.get("name"))
        return total

    async def run(self) -> None:
        log.info("sampler up (tick %ss)", TICK_S)
        while not self._stop:
            try:
                await self.tick()
            except Exception:
                log.exception("sampler tick failed")
            await asyncio.sleep(TICK_S)
