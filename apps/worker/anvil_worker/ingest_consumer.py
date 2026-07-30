"""Redis Stream → Postgres batch writer (plan/ingestion.md §3, ADR-0002/0003).

Delivery is at-least-once, so this module's job is to make *duplicates
harmless* rather than to prevent them. Three mechanisms carry that weight:

1. **Guarded upserts** — client-supplied IDs make cross-tenant overwrite and
   event reordering possible; the tenant and monotonic guards close both
   (checklist rows 1–2).
2. **Reclaim** — entries a crashed consumer never acked are picked up by a
   surviving one, which is what makes "kill the worker mid-run" lose nothing.
3. **Dead-letter queue** — a message that has failed 5 deliveries is poison and
   is set aside, so one bad payload can't crash-loop every replica
   (checklist row 4).
"""

import asyncio
import json
import logging
import socket
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import tracing
from .config import get_settings
from .db import transaction
from .derive import derive_trace_fields, parse_ts
from .metrics import (
    ABANDONED_TRACES,
    CONSUMER_LAG,
    DLQ_MESSAGES,
    PERSIST_BATCH_SECONDS,
    RECLAIMED_MESSAGES,
    SPANS_PERSISTED,
    TENANT_GUARD_BLOCKS,
    TRACES_PERSISTED,
)
from .streams import DLQ_STREAM, PERSISTERS_GROUP, TRACES_STREAM
from .tables import spans as spans_table
from .tables import traces as traces_table

log = logging.getLogger(__name__)

# Postgres caps a single statement at 32767 bind parameters. A full 500-trace
# batch carries several thousand spans, which multiplied by the column count
# sails past that — so multi-row inserts are split into statements that fit.
# All chunks still run inside one transaction, so batch atomicity is unchanged.
MAX_BIND_PARAMS = 32_767


def _chunked(rows: list[dict], columns: int):
    size = max(1, (MAX_BIND_PARAMS // max(columns, 1)) - 1)
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


# Columns refreshed when a trace is re-delivered. `id` and `project_id` are
# excluded on purpose: identity and tenancy are never rewritten by an update.
_TRACE_UPDATE_COLUMNS = (
    "name", "status", "input", "output", "metadata", "search_text", "model",
    "total_tokens_in", "total_tokens_out", "total_cost_usd", "latency_ms",
    "session_id", "user_ref", "started_at", "ended_at",
)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _trace_row(trace: dict) -> dict | None:
    """Payload → column dict. Returns None if the message is unusable, which
    routes it to the DLQ rather than failing the batch."""
    trace_id = _as_uuid(trace.get("id"))
    project_id = _as_uuid(trace.get("project_id"))
    started_at = parse_ts(trace.get("started_at"))
    if trace_id is None or project_id is None or started_at is None:
        return None

    return {
        "id": trace_id,
        "project_id": project_id,
        "name": str(trace.get("name") or "")[:512],
        "status": trace.get("status") or ("ok" if trace.get("ended_at") else "in_progress"),
        "input": trace.get("input"),
        "output": trace.get("output"),
        "metadata": trace.get("metadata"),
        "search_text": trace.get("search_text"),
        "model": trace.get("model"),
        "total_tokens_in": trace.get("total_tokens_in"),
        "total_tokens_out": trace.get("total_tokens_out"),
        "total_cost_usd": trace.get("total_cost_usd"),
        "latency_ms": trace.get("latency_ms"),
        "session_id": trace.get("session_id"),
        "user_ref": trace.get("user_ref"),
        "started_at": started_at,
        "ended_at": parse_ts(trace.get("ended_at")),
    }


def _span_row(span: dict, trace_id: uuid.UUID, project_id: uuid.UUID) -> dict | None:
    span_id = _as_uuid(span.get("id"))
    started_at = parse_ts(span.get("started_at"))
    if span_id is None or started_at is None:
        return None
    return {
        "id": span_id,
        "trace_id": trace_id,
        "parent_span_id": _as_uuid(span.get("parent_span_id")),
        "project_id": project_id,
        "name": str(span.get("name") or "")[:512],
        "type": span.get("type") or "custom",
        "input": span.get("input"),
        "output": span.get("output"),
        "attributes": span.get("attributes"),
        "tokens_in": span.get("tokens_in"),
        "tokens_out": span.get("tokens_out"),
        "cost_usd": span.get("cost_usd"),
        "status": span.get("status") or "ok",
        "error": span.get("error"),
        "started_at": started_at,
        "ended_at": parse_ts(span.get("ended_at")),
    }


def _merge_duplicate_traces(rows: list[dict]) -> list[dict]:
    """Collapse repeats of one trace id within a batch.

    Postgres refuses to let a single INSERT … ON CONFLICT DO UPDATE touch the
    same row twice, and a batch legitimately carries both the `in_progress` and
    the completed event for one trace. Completion wins, mirroring the monotonic
    guard that would otherwise have to arbitrate.
    """
    merged: dict[uuid.UUID, dict] = {}
    for row in rows:
        existing = merged.get(row["id"])
        if existing is None:
            merged[row["id"]] = row
            continue
        if existing.get("ended_at") is not None and row.get("ended_at") is None:
            continue  # keep the completion; discard the earlier in_progress
        merged[row["id"]] = row
    return list(merged.values())


class IngestConsumer:
    def __init__(self, redis: Redis, consumer_name: str | None = None):
        self.redis = redis
        self.settings = get_settings()
        # Consumer name identifies this replica in the group so its pending
        # entries can be reclaimed by name after a crash.
        self.name = consumer_name or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._running = False

    # ── stream plumbing ──────────────────────────────────────────────────

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                TRACES_STREAM, PERSISTERS_GROUP, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_batch(self) -> list[tuple[str, dict]]:
        """Up to `consumer_batch_size` messages, or whatever arrived within
        `consumer_block_ms` — whichever comes first."""
        response = await self.redis.xreadgroup(
            PERSISTERS_GROUP,
            self.name,
            {TRACES_STREAM: ">"},
            count=self.settings.consumer_batch_size,
            block=self.settings.consumer_block_ms,
        )
        if not response:
            return []
        return [(msg_id, fields) for _stream, entries in response for msg_id, fields in entries]

    async def reclaim_pending(self) -> list[tuple[str, dict]]:
        """Take over entries abandoned by a dead consumer, dead-lettering any
        that have already failed too many times.

        The delivery-count check happens *before* reprocessing: a message that
        killed five consumers will kill a sixth.
        """
        try:
            pending = await self.redis.xpending_range(
                TRACES_STREAM,
                PERSISTERS_GROUP,
                min="-",
                max="+",
                count=self.settings.consumer_batch_size,
                idle=self.settings.reclaim_idle_ms,
            )
        except ResponseError:
            return []

        if not pending:
            return []

        poison_ids, retry_ids = [], []
        for entry in pending:
            if entry["times_delivered"] >= self.settings.max_deliveries:
                poison_ids.append(entry["message_id"])
            else:
                retry_ids.append(entry["message_id"])

        for message_id in poison_ids:
            await self._dead_letter(message_id, reason="max_deliveries_exceeded")

        if not retry_ids:
            return []

        claimed = await self.redis.xclaim(
            TRACES_STREAM,
            PERSISTERS_GROUP,
            self.name,
            min_idle_time=self.settings.reclaim_idle_ms,
            message_ids=retry_ids,
        )
        if claimed:
            RECLAIMED_MESSAGES.inc(len(claimed))
            log.info("reclaimed %d pending entries", len(claimed))
        return [(msg_id, fields) for msg_id, fields in claimed]

    async def _dead_letter(self, message_id: str, reason: str) -> None:
        """Park a message on the DLQ and ack the original so the group stops
        redelivering it. Payload is preserved for replay after a fix."""
        payload = ""
        try:
            entries = await self.redis.xrange(TRACES_STREAM, min=message_id, max=message_id)
            if entries:
                payload = entries[0][1].get("payload", "")
        except RedisError:
            log.warning("could not read poison message %s for DLQ", message_id)

        await self.redis.xadd(
            DLQ_STREAM,
            {"payload": payload, "reason": reason, "original_id": message_id},
            maxlen=self.settings.dlq_maxlen,
            approximate=True,
        )
        await self.redis.xack(TRACES_STREAM, PERSISTERS_GROUP, message_id)
        await self.redis.xdel(TRACES_STREAM, message_id)
        DLQ_MESSAGES.inc()
        log.warning("dead-lettered message %s (%s)", message_id, reason)

    # ── persistence ──────────────────────────────────────────────────────

    async def persist(self, traces: list[dict]) -> tuple[int, int]:
        """Guarded upsert of traces + spans in one transaction.

        Returns (traces_written, spans_written).
        """
        trace_rows, span_rows_by_trace = [], {}

        for trace in traces:
            derive_trace_fields(trace)
            row = _trace_row(trace)
            if row is None:
                continue
            trace_rows.append(row)
            span_rows = []
            for span in trace.get("spans") or []:
                span_row = _span_row(span, row["id"], row["project_id"])
                if span_row is not None:
                    span_rows.append(span_row)
            if span_rows:
                span_rows_by_trace.setdefault(row["id"], []).extend(span_rows)

        if not trace_rows:
            return 0, 0

        trace_rows = _merge_duplicate_traces(trace_rows)
        submitted = {row["id"]: row["project_id"] for row in trace_rows}

        async with transaction() as conn:
            for chunk in _chunked(trace_rows, len(traces_table.c)):
                stmt = pg_insert(traces_table).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[traces_table.c.id],
                    set_={col: stmt.excluded[col] for col in _TRACE_UPDATE_COLUMNS},
                    where=and_(
                        # Tenant guard: a client cannot overwrite another
                        # project's trace by guessing its ID (checklist row 1).
                        traces_table.c.project_id == stmt.excluded.project_id,
                        # Monotonic guard: a completed trace never regresses to
                        # in_progress, however the events are ordered across
                        # consumers (checklist row 2).
                        or_(
                            stmt.excluded.ended_at.isnot(None),
                            traces_table.c.ended_at.is_(None),
                        ),
                    ),
                )
                await conn.execute(stmt)

            # Whose rows actually exist, and under which project? This settles
            # both which spans are safe to write and which upserts a tenant
            # mismatch blocked.
            owner_rows = (
                await conn.execute(
                    select(traces_table.c.id, traces_table.c.project_id).where(
                        traces_table.c.id.in_(list(submitted))
                    )
                )
            ).all()
            owners = {row.id: row.project_id for row in owner_rows}

            allowed_trace_ids = set()
            for trace_id, project_id in submitted.items():
                owner = owners.get(trace_id)
                if owner is None:
                    continue
                if owner != project_id:
                    # Blocked by the tenant guard: count and log, don't error
                    # the batch — the rest of it is legitimate traffic.
                    TENANT_GUARD_BLOCKS.labels(project=str(project_id)).inc()
                    log.warning(
                        "tenant guard blocked write to trace %s: claimed by project %s, "
                        "owned by project %s",
                        trace_id, project_id, owner,
                    )
                    continue
                allowed_trace_ids.add(trace_id)

            span_rows = [
                row
                for trace_id, rows in span_rows_by_trace.items()
                if trace_id in allowed_trace_ids
                for row in rows
            ]
            # Deduplicate within the batch; last write of an id wins.
            span_rows = list({row["id"]: row for row in span_rows}.values())

            spans_written = 0
            for chunk in _chunked(span_rows, len(spans_table.c)):
                span_stmt = pg_insert(spans_table).values(chunk)
                # Spans are immutable once written: a redelivery is a no-op.
                span_stmt = span_stmt.on_conflict_do_nothing(
                    index_elements=[spans_table.c.id]
                )
                result = await conn.execute(span_stmt)
                spans_written += result.rowcount or 0

        traces_written = len(allowed_trace_ids)
        TRACES_PERSISTED.inc(traces_written)
        SPANS_PERSISTED.inc(spans_written)
        return traces_written, spans_written

    # ── batch processing ─────────────────────────────────────────────────

    async def process(self, entries: list[tuple[str, dict]]) -> None:
        if not entries:
            return

        started = time.perf_counter()
        traces, message_ids, undecodable = [], [], []
        # W3C parents the API stamped on each message, so one ingest request is a
        # single trace across the queue (plan/observability.md §4). Empty when OTel
        # is off, which is the default.
        traceparents: list[str] = []

        for message_id, fields in entries:
            try:
                traces.append(json.loads(fields["payload"]))
                message_ids.append(message_id)
                if parent := fields.get("traceparent"):
                    traceparents.append(parent)
            except (KeyError, json.JSONDecodeError):
                # Malformed beyond repair — no number of retries fixes it.
                undecodable.append(message_id)

        for message_id in undecodable:
            await self._dead_letter(message_id, reason="undecodable_payload")

        if traces:
            with tracing.persist_span("ingest.persist", traceparents, len(traces)):
                written, spans_written = await self.persist(traces)
            # Ack only after the transaction commits. A crash before this point
            # leaves the entries pending, and reclaim replays them — the source
            # of at-least-once delivery, and why the guards must be idempotent.
            await self.redis.xack(TRACES_STREAM, PERSISTERS_GROUP, *message_ids)
            await self.redis.xdel(TRACES_STREAM, *message_ids)
            log.info(
                "persisted %d traces / %d spans from %d messages",
                written, spans_written, len(message_ids),
            )

        PERSIST_BATCH_SECONDS.observe(time.perf_counter() - started)

    async def refresh_lag(self) -> None:
        try:
            summary = await self.redis.xpending(TRACES_STREAM, PERSISTERS_GROUP)
            CONSUMER_LAG.set(summary["pending"] if summary else 0)
        except (RedisError, KeyError, TypeError):
            pass

    async def run(self) -> None:
        await self.ensure_group()
        # Startup reclaim: entries a previous incarnation of this worker left
        # pending are the ones most likely to be lost otherwise.
        await self.process(await self.reclaim_pending())

        self._running = True
        last_reclaim = time.monotonic()
        log.info("ingest consumer %s reading %s", self.name, TRACES_STREAM)

        while self._running:
            try:
                await self.process(await self.read_batch())

                if time.monotonic() - last_reclaim > self.settings.reclaim_interval_s:
                    await self.process(await self.reclaim_pending())
                    await self.refresh_lag()
                    last_reclaim = time.monotonic()
            except asyncio.CancelledError:
                raise
            except RedisError:
                log.exception("redis error in consumer loop; backing off")
                await asyncio.sleep(1)
            except Exception:
                # A bad batch must not kill the loop; the entries stay pending
                # and their delivery count climbs toward the DLQ threshold.
                log.exception("unexpected error in consumer loop")
                await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False


async def reap_stale_traces() -> int:
    """Mark traces whose client died mid-request as `abandoned`.

    A late completion still wins: `abandoned` leaves `ended_at` null, so the
    monotonic guard admits the real completion whenever it turns up
    (checklist row 3).
    """
    settings = get_settings()
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "UPDATE traces SET status = 'abandoned' "
                "WHERE status = 'in_progress' "
                "AND started_at < now() - make_interval(mins => :minutes)"
            ),
            {"minutes": settings.stale_trace_minutes},
        )
        count = result.rowcount or 0
    if count:
        ABANDONED_TRACES.inc(count)
        log.info("reaper marked %d stale traces abandoned", count)
    return count


async def run_reaper() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.reaper_interval_s)
        try:
            await reap_stale_traces()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stale-trace reaper failed; will retry next cycle")
