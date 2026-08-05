"""`ingest:feedback` → Postgres (plan/evaluation.md §7, checklist rows 12–13).

This consumer exists because of one race. A thumbs-down is submitted milliseconds
after the answer is rendered, while the trace it points at is still sitting in the
`ingest:traces` buffer waiting for the persister. A synchronous insert would hit
a foreign-key violation and throw away the single most valuable signal a customer
can send us. So:

1. **Defer, don't fail.** A row whose trace hasn't landed goes into a Redis sorted
   set keyed by its next attempt time and is retried with a widening delay. After
   `feedback_retry_window_s` it is dead-lettered — the trace is never coming.
2. **Upsert, don't append.** Repeat feedback from one `user_ref` updates in place,
   so toggling 👎 → 👍 leaves one row rather than two contradictory ones.
3. **Tenant check at persist.** The trace must belong to the project the
   submitting key granted; otherwise the row is dropped and counted. Feedback
   carries a client-supplied trace id, so this is the same guessing attack the
   ingest consumer's tenant guard closes.

Why a sorted set rather than leaving entries pending on the stream: a stream has
no notion of "not before time T". Redelivery timing would be `reclaim_idle_ms`,
the delivery counter would dead-letter a perfectly healthy row after
`max_deliveries` attempts, and every undue entry would be re-read on every pass.
"""

import asyncio
import json
import logging
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import get_settings
from .db import transaction
from .derive import parse_ts
from .metrics import (
    FEEDBACK_DEFERRED,
    FEEDBACK_DEFERRED_DEPTH,
    FEEDBACK_DROPPED,
    FEEDBACK_PERSISTED,
    RECLAIMED_MESSAGES,
)
from .streams import DLQ_STREAM, FEEDBACK_DEFERRED_KEY, FEEDBACK_GROUP, FEEDBACK_STREAM
from .tables import feedback as feedback_table
from .tables import traces as traces_table

log = logging.getLogger(__name__)


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class FeedbackEvent:
    """One validated submission plus the clock that bounds its retries."""

    __slots__ = ("id", "project_id", "trace_id", "rating", "comment", "user_ref", "enqueued_at")

    def __init__(
        self,
        id: uuid.UUID,
        project_id: uuid.UUID,
        trace_id: uuid.UUID,
        rating: int,
        comment: str | None,
        user_ref: str | None,
        enqueued_at: datetime,
    ) -> None:
        self.id = id
        self.project_id = project_id
        self.trace_id = trace_id
        self.rating = rating
        self.comment = comment
        self.user_ref = user_ref
        self.enqueued_at = enqueued_at

    @classmethod
    def parse(cls, payload: dict) -> "FeedbackEvent | None":
        """Returns None for anything unusable, which routes the message to the
        DLQ. No amount of retrying repairs a missing trace id."""
        event_id = _as_uuid(payload.get("id"))
        project_id = _as_uuid(payload.get("project_id"))
        trace_id = _as_uuid(payload.get("trace_id"))
        enqueued_at = parse_ts(payload.get("enqueued_at"))
        rating = payload.get("rating")
        if None in (event_id, project_id, trace_id) or rating not in (-1, 1):
            return None
        if enqueued_at is not None and enqueued_at.tzinfo is None:
            # A naive timestamp would make every age subtraction raise. UTC
            # everywhere is the convention (plan.md), so assume it.
            enqueued_at = enqueued_at.replace(tzinfo=timezone.utc)
        return cls(
            id=event_id,
            project_id=project_id,
            trace_id=trace_id,
            rating=int(rating),
            comment=payload.get("comment"),
            user_ref=payload.get("user_ref"),
            # A payload with no usable timestamp is treated as brand new. The
            # alternative — treating it as expired — would silently discard
            # feedback over a clock-format bug.
            enqueued_at=enqueued_at or datetime.now(timezone.utc),
        )

    def to_payload(self) -> dict:
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "trace_id": str(self.trace_id),
            "rating": self.rating,
            "comment": self.comment,
            "user_ref": self.user_ref,
            "enqueued_at": self.enqueued_at.isoformat(),
        }

    def member(self) -> str:
        """Deferred-set member. Deterministic (`sort_keys`) so re-deferring the
        same event updates its score instead of adding a second copy — that is
        what keeps the retry set from growing one entry per attempt."""
        return json.dumps(self.to_payload(), sort_keys=True)

    def age_s(self, now: datetime) -> float:
        return (now - self.enqueued_at).total_seconds()

    def row(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "trace_id": self.trace_id,
            "rating": self.rating,
            "comment": self.comment,
            "user_ref": self.user_ref,
        }


def _dedupe(events: list[FeedbackEvent]) -> list[FeedbackEvent]:
    """Collapse repeats within one batch.

    Postgres refuses to let a single `INSERT … ON CONFLICT DO UPDATE` touch the
    same row twice, and a batch legitimately carries a user's 👎 followed by
    their 👍. Last write wins, matching what two separate batches would have
    produced.
    """
    by_key: dict[tuple, FeedbackEvent] = {}
    for event in events:
        # Identified feedback collides on (trace, user); anonymous feedback only
        # on its own id, mirroring the partial unique index.
        key = (
            (event.trace_id, event.user_ref)
            if event.user_ref is not None
            else (event.id,)
        )
        by_key[key] = event
    return list(by_key.values())


async def persist_feedback(events: list[FeedbackEvent]) -> tuple[int, list[FeedbackEvent]]:
    """Write what can be written. Returns (persisted, needs_retry).

    Rows whose trace exists under another project are dropped here and counted —
    never returned for retry, because waiting changes nothing about who owns the
    trace.
    """
    if not events:
        return 0, []

    events = _dedupe(events)
    trace_ids = {event.trace_id for event in events}

    async with transaction() as conn:
        owners = {
            row.id: row.project_id
            for row in (
                await conn.execute(
                    select(traces_table.c.id, traces_table.c.project_id).where(
                        traces_table.c.id.in_(list(trace_ids))
                    )
                )
            ).all()
        }

        writable, retry = [], []
        for event in events:
            owner = owners.get(event.trace_id)
            if owner is None:
                retry.append(event)
                continue
            if owner != event.project_id:
                FEEDBACK_DROPPED.labels(cause="tenant_mismatch").inc()
                log.warning(
                    "feedback for trace %s claimed by project %s, owned by project %s "
                    "— dropped",
                    event.trace_id, event.project_id, owner,
                )
                continue
            writable.append(event)

        # Two statements because a single INSERT takes one ON CONFLICT clause and
        # these two conflict targets are genuinely different. The partial unique
        # index only covers `user_ref IS NOT NULL`, so anonymous rows fall back to
        # the primary key — which is why the API mints the id rather than the
        # worker: without it, a redelivered anonymous submission would insert a
        # second row.
        identified = [e.row() for e in writable if e.user_ref is not None]
        anonymous = [e.row() for e in writable if e.user_ref is None]

        if identified:
            stmt = pg_insert(feedback_table).values(identified)
            await conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[feedback_table.c.trace_id, feedback_table.c.user_ref],
                    index_where=feedback_table.c.user_ref.isnot(None),
                    set_={
                        "rating": stmt.excluded.rating,
                        "comment": stmt.excluded.comment,
                    },
                )
            )
        if anonymous:
            stmt = pg_insert(feedback_table).values(anonymous)
            # A redelivery is a no-op: the row is already there, identically.
            await conn.execute(
                stmt.on_conflict_do_nothing(index_elements=[feedback_table.c.id])
            )

    if writable:
        FEEDBACK_PERSISTED.inc(len(writable))
    return len(writable), retry


class FeedbackConsumer:
    def __init__(self, redis: Redis, consumer_name: str | None = None):
        self.redis = redis
        self.settings = get_settings()
        self.name = consumer_name or f"{socket.gethostname()}-fb-{uuid.uuid4().hex[:8]}"
        self._running = False

    # ── stream plumbing ──────────────────────────────────────────────────

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                FEEDBACK_STREAM, FEEDBACK_GROUP, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_batch(self) -> list[tuple[str, dict]]:
        response = await self.redis.xreadgroup(
            FEEDBACK_GROUP,
            self.name,
            {FEEDBACK_STREAM: ">"},
            count=self.settings.feedback_batch_size,
            block=self.settings.consumer_block_ms,
        )
        if not response:
            return []
        return [(msg_id, fields) for _stream, entries in response for msg_id, fields in entries]

    async def reclaim_pending(self) -> list[tuple[str, dict]]:
        """Take over entries a dead consumer never acked.

        `process_stream` acks only after the writes commit, so a worker killed
        mid-batch leaves its entries pending — and with no reclaim they would sit
        there forever with nobody to read them. That is the one loss path this
        consumer would otherwise still have, since everything else it drops is
        dropped deliberately.

        The delivery-count check runs *before* reprocessing: a message that killed
        five consumers will kill a sixth.
        """
        try:
            pending = await self.redis.xpending_range(
                FEEDBACK_STREAM,
                FEEDBACK_GROUP,
                min="-",
                max="+",
                count=self.settings.feedback_batch_size,
                idle=self.settings.reclaim_idle_ms,
            )
        except ResponseError:
            return []

        if not pending:
            return []

        poison, retry = [], []
        for entry in pending:
            if entry["times_delivered"] >= self.settings.max_deliveries:
                poison.append(entry["message_id"])
            else:
                retry.append(entry["message_id"])

        for message_id in poison:
            await self._dead_letter_message(message_id, "max_deliveries_exceeded")

        if not retry:
            return []

        claimed = await self.redis.xclaim(
            FEEDBACK_STREAM,
            FEEDBACK_GROUP,
            self.name,
            min_idle_time=self.settings.reclaim_idle_ms,
            message_ids=retry,
        )
        if claimed:
            RECLAIMED_MESSAGES.inc(len(claimed))
            log.info("reclaimed %d pending feedback entries", len(claimed))
        return [(msg_id, fields) for msg_id, fields in claimed]

    async def _dead_letter(self, payload: str, reason: str) -> None:
        await self.redis.xadd(
            DLQ_STREAM,
            {"payload": payload, "reason": reason, "stream": FEEDBACK_STREAM},
            maxlen=self.settings.dlq_maxlen,
            approximate=True,
        )
        FEEDBACK_DROPPED.labels(cause=reason).inc()

    async def _dead_letter_message(self, message_id: str, reason: str) -> None:
        """Park a stream entry by id, preserving its payload for replay, and ack
        the original so the group stops redelivering it."""
        payload = ""
        try:
            entries = await self.redis.xrange(
                FEEDBACK_STREAM, min=message_id, max=message_id
            )
            if entries:
                payload = entries[0][1].get("payload", "")
        except RedisError:
            log.warning("could not read poison feedback message %s for DLQ", message_id)

        await self._dead_letter(payload, reason)
        await self.redis.xack(FEEDBACK_STREAM, FEEDBACK_GROUP, message_id)
        await self.redis.xdel(FEEDBACK_STREAM, message_id)

    # ── the deferred set ─────────────────────────────────────────────────

    def _next_delay_s(self, age_s: float) -> float:
        """Backoff derived from the row's own age rather than an attempt counter.

        Each retry happens roughly `age` seconds after the last one, so delays
        widen 1 → 2 → 4 → … up to the cap. Deriving it means the member stays
        byte-identical across attempts, which is what lets ZADD dedupe it.
        """
        return min(self.settings.feedback_retry_max_delay_s, max(1.0, age_s))

    async def defer(self, events: list[FeedbackEvent]) -> set[str]:
        """Schedule a retry for each event. Returns the members actually written.

        The return value is load-bearing for `process_deferred`: a row that has
        now outlived the window is dead-lettered here rather than rescheduled, and
        the caller must know that so it can clear the old entry. Without it, an
        expired member keeps its stale past-due score, gets pulled every cycle,
        and is dead-lettered on each one — forever.
        """
        if not events:
            return set()
        now = datetime.now(timezone.utc)
        depth = int(await self.redis.zcard(FEEDBACK_DEFERRED_KEY) or 0)

        mapping: dict[str, float] = {}
        for event in events:
            age = event.age_s(now)
            if age > self.settings.feedback_retry_window_s:
                # The trace was never coming. Dead-letter rather than retry
                # forever; the payload is preserved for replay after a fix.
                await self._dead_letter(
                    json.dumps(event.to_payload()), "retry_window_expired"
                )
                continue
            if depth + len(mapping) >= self.settings.feedback_deferred_max:
                await self._dead_letter(
                    json.dumps(event.to_payload()), "deferred_overflow"
                )
                continue
            mapping[event.member()] = time.time() + self._next_delay_s(age)

        if mapping:
            await self.redis.zadd(FEEDBACK_DEFERRED_KEY, mapping)
            FEEDBACK_DEFERRED.inc(len(mapping))
        return set(mapping)

    async def due_deferred(self) -> list[tuple[str, FeedbackEvent | None]]:
        """Members whose next-attempt time has passed, paired with their parsed
        event (None where the stored JSON is unusable)."""
        members = await self.redis.zrangebyscore(
            FEEDBACK_DEFERRED_KEY,
            min=0,
            max=time.time(),
            start=0,
            num=self.settings.feedback_batch_size,
        )
        out = []
        for member in members:
            try:
                out.append((member, FeedbackEvent.parse(json.loads(member))))
            except (json.JSONDecodeError, TypeError):
                out.append((member, None))
        return out

    async def refresh_deferred_depth(self) -> None:
        try:
            FEEDBACK_DEFERRED_DEPTH.set(
                int(await self.redis.zcard(FEEDBACK_DEFERRED_KEY) or 0)
            )
        except RedisError:
            pass

    # ── batch processing ─────────────────────────────────────────────────

    async def process_stream(self, entries: list[tuple[str, dict]]) -> int:
        """Handle a batch of fresh submissions. Returns rows persisted."""
        if not entries:
            return 0

        events, message_ids, undecodable = [], [], []
        for message_id, fields in entries:
            try:
                payload = json.loads(fields["payload"])
            except (KeyError, json.JSONDecodeError):
                undecodable.append((message_id, ""))
                continue
            event = FeedbackEvent.parse(payload)
            if event is None:
                undecodable.append((message_id, fields.get("payload", "")))
                continue
            events.append(event)
            message_ids.append(message_id)

        for message_id, payload in undecodable:
            await self._dead_letter(payload, "undecodable_payload")
            await self.redis.xack(FEEDBACK_STREAM, FEEDBACK_GROUP, message_id)
            await self.redis.xdel(FEEDBACK_STREAM, message_id)

        if not events:
            return 0

        persisted, retry = await persist_feedback(events)
        # Defer *before* acking: a crash between the two replays the batch from
        # the stream, which is harmless (the writes are idempotent). Acking first
        # and crashing would lose the deferred rows outright.
        await self.defer(retry)
        await self.redis.xack(FEEDBACK_STREAM, FEEDBACK_GROUP, *message_ids)
        await self.redis.xdel(FEEDBACK_STREAM, *message_ids)

        if retry:
            log.info(
                "persisted %d feedback rows, deferred %d awaiting their trace",
                persisted, len(retry),
            )
        return persisted

    async def process_deferred(self) -> int:
        """Retry rows whose trace was missing last time. Returns rows persisted."""
        due = await self.due_deferred()
        if not due:
            return 0

        events, members = [], []
        for member, event in due:
            if event is None:
                await self._dead_letter(member, "undecodable_payload")
                await self.redis.zrem(FEEDBACK_DEFERRED_KEY, member)
                continue
            events.append(event)
            members.append(member)

        if not events:
            return 0

        persisted, retry = await persist_feedback(events)
        # ZADD first, ZREM second. Re-deferred rows are rewritten with a later
        # score before the sweep below removes anything, so nothing can be
        # stranded — and a crash between the two costs a duplicate retry, not a
        # lost row. Everything else leaves the set: persisted, tenant-dropped,
        # dead-lettered, and collapsed duplicates alike.
        rescheduled = await self.defer(retry)
        settled = [m for m in members if m not in rescheduled]
        if settled:
            await self.redis.zrem(FEEDBACK_DEFERRED_KEY, *settled)
        if persisted:
            log.info("persisted %d deferred feedback rows after their trace landed", persisted)
        return persisted

    async def run(self) -> None:
        await self.ensure_group()
        # Startup reclaim: entries a previous incarnation of this worker left
        # pending are the ones most likely to be lost otherwise — and so the
        # likeliest place to meet a poison message. Non-fatal for the same reason
        # as the ingest consumer: raising here exits the whole worker before the
        # loop that drives the DLQ ever runs, and the next start repeats it.
        try:
            await self.process_stream(await self.reclaim_pending())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "startup reclaim failed; entering the read loop anyway "
                "(the entries stay pending and will be retried, then dead-lettered)"
            )

        self._running = True
        last_reclaim = time.monotonic()
        log.info("feedback consumer %s reading %s", self.name, FEEDBACK_STREAM)

        while self._running:
            try:
                await self.process_stream(await self.read_batch())
                await self.process_deferred()
                await self.refresh_deferred_depth()

                if time.monotonic() - last_reclaim > self.settings.reclaim_interval_s:
                    await self.process_stream(await self.reclaim_pending())
                    last_reclaim = time.monotonic()
            except asyncio.CancelledError:
                raise
            except RedisError:
                log.exception("redis error in feedback loop; backing off")
                await asyncio.sleep(1)
            except Exception:
                # Entries stay pending and the deferred set keeps its rows, so a
                # bad batch costs a cycle rather than the data.
                log.exception("unexpected error in feedback loop")
                await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False
