"""Retention sweep and usage rollups (plan/retention.md, ADR-0019/0022).

Two jobs on one daily loop, and they pull against each other — which is the
interesting thing about this module.

**Retention** deletes traces older than `RETENTION_DAYS`. Unset means keep
everything: an operator who has not asked for deletion never gets deletion, so
the window is `None` by default and no value is ever inferred. Deletes go in
batches of 5k with a sleep between, because one `DELETE` covering months of
traffic bloats the WAL and holds locks for the length of the sweep (checklist
row 23). Spans, feedback and monitor scores follow by `ON DELETE CASCADE`;
`dataset_items.source_trace_id` is `ON DELETE SET NULL`, so a curated dataset
outlives the traces it was built from (checklist row 24) — items carry their own
copy of the input, the reference output and the recorded output (ADR-0018).

**Usage rollups** upsert monthly per-org counts into `org_usage`, recomputed from
Postgres rather than counted in Redis: with no quota to enforce there is nothing
to enforce cheaply, and a recompute has no second source of truth to drift from.

The collision: a recompute reads the table retention just emptied, so replaying
it would overwrite June's 400k traces with 0 the first time June falls out of the
window. **Counters therefore only ever move up** (`GREATEST`, ADR-0022) —
`traces_ingested` is a fact about the past and cannot fall, and a smaller
recompute is evidence of deletion, not a correction.

At real scale, monthly declarative partitions turn expiry into a partition drop
instead of a delete. Explicitly out of scope for v1 (plan/architecture.md §8).
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import get_settings
from .db import transaction
from .metrics import (
    RETENTION_DELETED,
    RETENTION_SWEEP_SECONDS,
    USAGE_ROLLUPS,
)
from .tables import eval_results, eval_runs, org_usage, projects, traces

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cutoff(days: int, now: datetime | None = None) -> datetime:
    """The oldest `started_at` a trace may have and survive."""
    return (now or _now()) - timedelta(days=days)


# ---- retention ---------------------------------------------------------------


async def delete_batch(before: datetime, limit: int) -> int:
    """Delete up to `limit` traces started before `before`. Returns the count.

    Two-step — select ids, then delete by id — rather than
    `DELETE ... WHERE started_at < :before LIMIT`, which Postgres does not
    support. The subquery also keeps each statement's row count bounded and
    predictable, which is the entire point of batching.
    """
    async with transaction() as conn:
        ids = (
            await conn.execute(
                select(traces.c.id)
                .where(traces.c.started_at < before)
                # Oldest first: a sweep interrupted halfway has still removed the
                # data that was furthest out of policy.
                .order_by(traces.c.started_at)
                .limit(limit)
            )
        ).scalars().all()

        if not ids:
            return 0

        result = await conn.execute(delete(traces).where(traces.c.id.in_(list(ids))))
        return result.rowcount or 0


async def sweep(days: int | None = None, now: datetime | None = None) -> int:
    """One retention pass. Returns traces deleted.

    `days=None` deletes nothing and says so once — the default install must never
    lose data because a worker was started.
    """
    settings = get_settings()
    days = settings.retention_days if days is None else days
    if days is None:
        return 0
    if days < 1:
        # A window of 0 would delete everything including traffic still arriving.
        # Refuse rather than interpret it.
        log.error("RETENTION_DAYS=%s is not a usable window; nothing deleted", days)
        return 0

    before = cutoff(days, now)
    started = asyncio.get_running_loop().time()
    total = 0

    while True:
        deleted = await delete_batch(before, settings.retention_batch_size)
        if deleted == 0:
            break
        total += deleted
        RETENTION_DELETED.inc(deleted)
        # Breathe between batches so a long sweep leaves room for ingest to keep
        # committing; a tight loop of 5k-row deletes is a self-inflicted outage.
        await asyncio.sleep(settings.retention_batch_sleep_s)

    RETENTION_SWEEP_SECONDS.observe(asyncio.get_running_loop().time() - started)
    if total:
        log.info(
            "retention deleted %d traces started before %s (%d-day window)",
            total, before.isoformat(), days,
        )
    return total


# ---- usage rollups -----------------------------------------------------------


def month_start(value: datetime | date) -> date:
    """`date_trunc('month', …)` returns a timestamp; the column is a DATE."""
    day = value.date() if isinstance(value, datetime) else value
    return day.replace(day=1)


async def recompute_usage(now: datetime | None = None) -> int:
    """Upsert monthly per-org rollups. Returns rows written.

    Counters only move up (ADR-0022): `traces_ingested` means "ingested during
    that month", which retention cannot make smaller. `storage_bytes` is left
    null on purpose — per-org bytes are not honestly attributable.
    """
    now = now or _now()

    async with transaction() as conn:
        trace_month = func.date_trunc("month", traces.c.started_at)
        trace_rows = (
            await conn.execute(
                select(
                    projects.c.org_id,
                    trace_month.label("month"),
                    func.count().label("n"),
                )
                .select_from(traces)
                .join(projects, projects.c.id == traces.c.project_id)
                .group_by(projects.c.org_id, trace_month)
            )
        ).all()

        # Eval items are counted from results, not from `eval_runs.total`: a
        # cancelled or half-finished run promised more items than it graded, and
        # the panel is about what was actually spent.
        item_month = func.date_trunc("month", eval_results.c.created_at)
        item_rows = (
            await conn.execute(
                select(
                    projects.c.org_id,
                    item_month.label("month"),
                    func.count().label("n"),
                )
                .select_from(eval_results)
                .join(eval_runs, eval_runs.c.id == eval_results.c.run_id)
                .join(projects, projects.c.id == eval_runs.c.project_id)
                .group_by(projects.c.org_id, item_month)
            )
        ).all()

        totals: dict[tuple, dict] = {}
        for row in trace_rows:
            key = (row.org_id, month_start(row.month))
            totals.setdefault(key, {"traces": 0, "items": 0})["traces"] = row.n
        for row in item_rows:
            key = (row.org_id, month_start(row.month))
            totals.setdefault(key, {"traces": 0, "items": 0})["items"] = row.n

        if not totals:
            return 0

        values = [
            {
                "org_id": org_id,
                "month": month,
                "traces_ingested": counts["traces"],
                "eval_items": counts["items"],
                "updated_at": now,
            }
            for (org_id, month), counts in totals.items()
        ]

        stmt = pg_insert(org_usage).values(values)
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[org_usage.c.org_id, org_usage.c.month],
                set_={
                    # GREATEST is the whole of ADR-0022. Without it, the first
                    # retention sweep silently rewrites a busy month as zero.
                    "traces_ingested": func.greatest(
                        org_usage.c.traces_ingested, stmt.excluded.traces_ingested
                    ),
                    "eval_items": func.greatest(
                        org_usage.c.eval_items, stmt.excluded.eval_items
                    ),
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    USAGE_ROLLUPS.inc(len(values))
    log.info("usage rollups upserted for %d org-months", len(values))
    return len(values)


# ---- the loop ----------------------------------------------------------------


class Retention:
    """The daily pass. Rollups run whether or not retention is enabled, because
    the usage panel is useful to every operator; deletion is opt-in."""

    def __init__(self) -> None:
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> tuple[int, int]:
        deleted = 0
        try:
            deleted = await sweep()
        except Exception:
            # A failed sweep must not cost the rollups, or the next cycle.
            log.exception("retention sweep failed")

        rolled = 0
        try:
            rolled = await recompute_usage()
        except Exception:
            log.exception("usage rollup failed")

        try:
            await log_table_sizes()
        except Exception:
            log.debug("could not read table sizes", exc_info=True)
        return deleted, rolled

    async def run(self) -> None:
        settings = get_settings()
        if settings.retention_days is None:
            log.info(
                "retention idle (RETENTION_DAYS unset — traces are kept forever); "
                "usage rollups still run daily"
            )
        else:
            log.info("retention up (%d-day window, daily)", settings.retention_days)

        while not self._stop:
            await self.tick()
            # Sleep in short slices so `stop()` is honoured promptly rather than
            # up to a day later.
            slept = 0.0
            while slept < settings.retention_interval_s and not self._stop:
                await asyncio.sleep(min(5.0, settings.retention_interval_s - slept))
                slept += 5.0


async def log_table_sizes(top: int = 5) -> None:
    """Record the biggest tables once per daily pass.

    This is what `table_sizes()` is *for*. Growth is the thing this module exists
    to manage, and "the database is large" is a question an operator answers by
    looking backwards through logs — so the number has to have been written down
    before they came asking. One line a day is cheap enough to always emit.
    """
    sizes = await table_sizes()
    if not sizes:
        return
    biggest = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:top]
    log.info(
        "table sizes: %s",
        ", ".join(f"{name} {bytes_ / 1_048_576:.1f}MB" for name, bytes_ in biggest),
    )


async def table_sizes() -> dict[str, int]:
    """Total bytes per table, for an operator diagnosing growth.

    Deliberately *not* written into `org_usage.storage_bytes`: this is per table
    across every tenant, and slicing it per org would be a guess (ADR-0022).
    """
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT relname, pg_total_relation_size(c.oid) AS bytes "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "ORDER BY bytes DESC"
                )
            )
        ).all()
    return {r.relname: int(r.bytes) for r in rows}
