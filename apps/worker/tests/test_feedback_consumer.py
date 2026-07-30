"""Feedback persistence and the race it exists to survive (Phase 9,
plan/evaluation.md §7, checklist rows 12–13).

The load-bearing behaviour under test is that **feedback arriving before its
trace is not an error**. Everything else here — the upsert, the tenant guard, the
retry window — falls out of that one decision.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from anvil_worker.db import engine
from anvil_worker.feedback_consumer import FeedbackConsumer, FeedbackEvent
from anvil_worker.streams import (
    DLQ_STREAM,
    FEEDBACK_DEFERRED_KEY,
    FEEDBACK_STREAM,
)

from .conftest import requires_services

pytestmark = requires_services


def now() -> datetime:
    return datetime.now(timezone.utc)


async def insert_trace(project_id: uuid.UUID, trace_id: uuid.UUID | None = None) -> uuid.UUID:
    trace_id = trace_id or uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, started_at) "
                "VALUES (:id, :project_id, 'ask', 'ok', :started_at)"
            ),
            {"id": trace_id, "project_id": project_id, "started_at": now()},
        )
    return trace_id


def payload(
    project_id: uuid.UUID,
    trace_id: uuid.UUID,
    *,
    rating: int = -1,
    comment: str | None = None,
    user_ref: str | None = None,
    age_s: float = 0.0,
    feedback_id: uuid.UUID | None = None,
) -> dict:
    return {
        "id": str(feedback_id or uuid.uuid4()),
        "project_id": str(project_id),
        "trace_id": str(trace_id),
        "rating": rating,
        "comment": comment,
        "user_ref": user_ref,
        "enqueued_at": (now() - timedelta(seconds=age_s)).isoformat(),
    }


async def rows() -> list[dict]:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT id, trace_id, project_id, rating, comment, user_ref "
            "FROM feedback ORDER BY created_at"
        )
        return [dict(r._mapping) for r in result]


async def dlq(redis_client) -> list[dict]:
    return [fields for _id, fields in await redis_client.xrange(DLQ_STREAM)]


@pytest.fixture
async def fb(redis_client):
    consumer = FeedbackConsumer(redis_client, consumer_name="test-feedback")
    await consumer.ensure_group()
    return consumer


async def publish(redis_client, *payloads: dict) -> None:
    for item in payloads:
        await redis_client.xadd(FEEDBACK_STREAM, {"payload": json.dumps(item)})


# ---- the happy path ---------------------------------------------------------


async def test_feedback_persists_when_the_trace_exists(fb, redis_client, project_factory):
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await publish(redis_client, payload(project_id, trace_id, comment="wrong date"))

    assert await fb.process_stream(await fb.read_batch()) == 1
    stored = await rows()
    assert len(stored) == 1
    assert stored[0]["trace_id"] == trace_id
    assert stored[0]["rating"] == -1
    assert stored[0]["comment"] == "wrong date"
    # Nothing left behind: entry acked and deleted, nothing deferred.
    assert await redis_client.xlen(FEEDBACK_STREAM) == 0
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0


async def test_redelivery_of_anonymous_feedback_does_not_duplicate(
    fb, redis_client, project_factory
):
    """The partial unique index only covers `user_ref IS NOT NULL`, so anonymous
    rows rely on the API-minted id as their idempotency key."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    event = payload(project_id, trace_id, user_ref=None)

    await publish(redis_client, event)
    await fb.process_stream(await fb.read_batch())
    await publish(redis_client, event)  # at-least-once delivery
    await fb.process_stream(await fb.read_batch())

    assert len(await rows()) == 1


# ---- the race (checklist row 12) --------------------------------------------


async def test_feedback_arriving_before_its_trace_is_deferred_then_persists(
    fb, redis_client, project_factory
):
    """The whole reason this consumer exists. A thumbs-down beats its trace to
    the database; the row must land once the trace does, not be refused."""
    project_id = await project_factory()
    trace_id = uuid.uuid4()  # not inserted yet
    await publish(redis_client, payload(project_id, trace_id, comment="too slow"))

    assert await fb.process_stream(await fb.read_batch()) == 0
    assert await rows() == []
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 1
    # The stream entry is settled — the deferred set owns the retry now.
    assert await redis_client.xlen(FEEDBACK_STREAM) == 0

    # Not due yet, so a retry now is a no-op rather than a hot spin.
    assert await fb.process_deferred() == 0
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 1

    await insert_trace(project_id, trace_id)
    # Make it due without sleeping: the clock is the mechanism, not the subject.
    await redis_client.zadd(
        FEEDBACK_DEFERRED_KEY,
        {m: 0 for m in await redis_client.zrange(FEEDBACK_DEFERRED_KEY, 0, -1)},
    )

    assert await fb.process_deferred() == 1
    stored = await rows()
    assert len(stored) == 1
    assert stored[0]["comment"] == "too slow"
    # Cleared, so the next cycle doesn't re-persist it forever.
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0


async def test_redeferring_does_not_grow_the_set(fb, redis_client, project_factory):
    """Backoff is derived from the row's age rather than an attempt counter, which
    keeps the member byte-identical so ZADD updates it in place."""
    project_id = await project_factory()
    await publish(redis_client, payload(project_id, uuid.uuid4()))
    await fb.process_stream(await fb.read_batch())

    for _ in range(4):
        await redis_client.zadd(
            FEEDBACK_DEFERRED_KEY,
            {m: 0 for m in await redis_client.zrange(FEEDBACK_DEFERRED_KEY, 0, -1)},
        )
        await fb.process_deferred()
        assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 1


async def test_backoff_widens_with_age(fb):
    """1 → 2 → 4 … up to the cap. A flat 1 s retry over a ten-minute window is
    600 pointless queries per orphaned row."""
    assert fb._next_delay_s(0.0) == 1.0
    assert fb._next_delay_s(2.0) == 2.0
    assert fb._next_delay_s(8.0) == 8.0
    assert fb._next_delay_s(10_000.0) == fb.settings.feedback_retry_max_delay_s


async def test_trace_that_never_arrives_is_dead_lettered(fb, redis_client, project_factory):
    """After the window, retrying forever is the wrong answer — the trace is not
    coming. The payload is preserved on the DLQ for replay."""
    project_id = await project_factory()
    trace_id = uuid.uuid4()
    await publish(
        redis_client,
        payload(project_id, trace_id, age_s=fb.settings.feedback_retry_window_s + 30),
    )

    assert await fb.process_stream(await fb.read_batch()) == 0
    assert await rows() == []
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0

    parked = await dlq(redis_client)
    assert len(parked) == 1
    assert parked[0]["reason"] == "retry_window_expired"
    assert parked[0]["stream"] == FEEDBACK_STREAM
    assert json.loads(parked[0]["payload"])["trace_id"] == str(trace_id)


async def test_expired_deferred_row_leaves_the_set(fb, redis_client, project_factory):
    """Regression: dead-lettering an expired member without removing it leaves a
    past-due score, so it is pulled and dead-lettered again every cycle."""
    project_id = await project_factory()
    await publish(redis_client, payload(project_id, uuid.uuid4()))
    await fb.process_stream(await fb.read_batch())
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 1

    # Age the stored payload past the window, and make it due.
    member = (await redis_client.zrange(FEEDBACK_DEFERRED_KEY, 0, -1))[0]
    aged = json.loads(member)
    aged["enqueued_at"] = (
        now() - timedelta(seconds=fb.settings.feedback_retry_window_s + 60)
    ).isoformat()
    await redis_client.delete(FEEDBACK_DEFERRED_KEY)
    await redis_client.zadd(FEEDBACK_DEFERRED_KEY, {json.dumps(aged, sort_keys=True): 0})

    await fb.process_deferred()
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0
    assert len(await dlq(redis_client)) == 1

    # And it stays gone rather than being re-parked on every pass.
    await fb.process_deferred()
    assert len(await dlq(redis_client)) == 1


async def test_deferred_set_is_bounded(fb, redis_client, project_factory, monkeypatch):
    """Feedback naming trace ids that will never exist is the abuse shape here.
    An unbounded retry set would let it grow until Redis is the outage."""
    monkeypatch.setattr(fb.settings, "feedback_deferred_max", 2, raising=False)
    project_id = await project_factory()
    await publish(redis_client, *[payload(project_id, uuid.uuid4()) for _ in range(5)])

    await fb.process_stream(await fb.read_batch())
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 2
    overflow = [d for d in await dlq(redis_client) if d["reason"] == "deferred_overflow"]
    assert len(overflow) == 3


# ---- repeat feedback (checklist row 13) -------------------------------------


async def test_repeat_feedback_from_one_user_upserts(fb, redis_client, project_factory):
    """Toggling 👎 → 👍 must leave one row, not two contradictory ones."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)

    await publish(
        redis_client,
        payload(project_id, trace_id, rating=-1, comment="wrong", user_ref="user-1"),
    )
    await fb.process_stream(await fb.read_batch())

    await publish(
        redis_client,
        payload(project_id, trace_id, rating=1, comment="actually fine", user_ref="user-1"),
    )
    await fb.process_stream(await fb.read_batch())

    stored = await rows()
    assert len(stored) == 1
    assert stored[0]["rating"] == 1
    assert stored[0]["comment"] == "actually fine"


async def test_two_users_on_one_trace_are_two_rows(fb, redis_client, project_factory):
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await publish(
        redis_client,
        payload(project_id, trace_id, rating=-1, user_ref="user-1"),
        payload(project_id, trace_id, rating=1, user_ref="user-2"),
    )
    await fb.process_stream(await fb.read_batch())
    assert len(await rows()) == 2


async def test_repeat_feedback_inside_one_batch_collapses(fb, redis_client, project_factory):
    """Postgres refuses to let one INSERT … ON CONFLICT touch a row twice, and a
    batch legitimately carries a user's 👎 then their 👍. Last write wins."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await publish(
        redis_client,
        payload(project_id, trace_id, rating=-1, user_ref="user-1"),
        payload(project_id, trace_id, rating=1, user_ref="user-1"),
    )
    await fb.process_stream(await fb.read_batch())

    stored = await rows()
    assert len(stored) == 1
    assert stored[0]["rating"] == 1


async def test_anonymous_feedback_is_not_collapsed(fb, redis_client, project_factory):
    """Two end users with no `user_ref` are two independent votes; the partial
    index deliberately does not unify them."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await publish(
        redis_client,
        payload(project_id, trace_id, rating=-1),
        payload(project_id, trace_id, rating=-1),
    )
    await fb.process_stream(await fb.read_batch())
    assert len(await rows()) == 2


# ---- tenancy ----------------------------------------------------------------


async def test_feedback_for_another_tenants_trace_is_dropped(
    fb, redis_client, project_factory
):
    """Feedback carries a client-supplied trace id, so this is the same guessing
    attack the ingest tenant guard closes. Dropped and counted, never deferred:
    waiting changes nothing about who owns the trace."""
    mine = await project_factory("Mine")
    theirs = await project_factory("Theirs")
    trace_id = await insert_trace(theirs)

    await publish(redis_client, payload(mine, trace_id, rating=-1))
    assert await fb.process_stream(await fb.read_batch()) == 0
    assert await rows() == []
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0


async def test_a_tenant_mismatch_does_not_sink_the_batch(fb, redis_client, project_factory):
    mine = await project_factory("Mine")
    theirs = await project_factory("Theirs")
    theirs_trace = await insert_trace(theirs)
    my_trace = await insert_trace(mine)

    await publish(
        redis_client,
        payload(mine, theirs_trace),
        payload(mine, my_trace, comment="legitimate"),
    )
    assert await fb.process_stream(await fb.read_batch()) == 1
    stored = await rows()
    assert [r["comment"] for r in stored] == ["legitimate"]


# ---- reclaim ----------------------------------------------------------------


async def test_a_dead_consumers_entries_are_reclaimed(
    redis_client, project_factory, monkeypatch
):
    """`process_stream` acks only after the writes commit, so a worker killed
    mid-batch leaves its entries pending. Without reclaim they would sit there
    forever with nobody to read them — the one loss path this consumer would
    otherwise still have."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)

    dead = FeedbackConsumer(redis_client, consumer_name="dead-worker")
    await dead.ensure_group()
    await publish(redis_client, payload(project_id, trace_id, comment="orphaned"))
    # Read without processing: exactly the state a SIGKILL leaves behind.
    assert len(await dead.read_batch()) == 1
    assert await rows() == []

    survivor = FeedbackConsumer(redis_client, consumer_name="survivor")
    # idle=0 so the test doesn't wait out the real 60 s reclaim delay. Settings
    # are lru_cached, so this is the one instance both consumers share.
    monkeypatch.setattr(survivor.settings, "reclaim_idle_ms", 0, raising=False)

    assert await survivor.process_stream(await survivor.reclaim_pending()) == 1
    assert [r["comment"] for r in await rows()] == ["orphaned"]


async def test_a_poison_entry_is_dead_lettered_not_reclaimed_forever(
    redis_client, project_factory, monkeypatch
):
    """A message that killed five consumers will kill a sixth, so the
    delivery-count check runs before reprocessing."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)

    consumer = FeedbackConsumer(redis_client, consumer_name="c1")
    await consumer.ensure_group()
    monkeypatch.setattr(consumer.settings, "reclaim_idle_ms", 0, raising=False)
    monkeypatch.setattr(consumer.settings, "max_deliveries", 2, raising=False)

    await publish(redis_client, payload(project_id, trace_id))
    await consumer.read_batch()  # delivery 1, never acked
    await consumer.reclaim_pending()  # delivery 2
    await consumer.reclaim_pending()  # over the threshold -> DLQ

    assert [d["reason"] for d in await dlq(redis_client)] == ["max_deliveries_exceeded"]
    assert await redis_client.xlen(FEEDBACK_STREAM) == 0


# ---- malformed input --------------------------------------------------------


async def test_undecodable_message_is_dead_lettered(fb, redis_client):
    await redis_client.xadd(FEEDBACK_STREAM, {"payload": "{not json"})
    assert await fb.process_stream(await fb.read_batch()) == 0
    assert [d["reason"] for d in await dlq(redis_client)] == ["undecodable_payload"]
    assert await redis_client.xlen(FEEDBACK_STREAM) == 0


async def test_missing_trace_id_is_dead_lettered_not_retried(fb, redis_client, project_factory):
    """No amount of waiting supplies a trace id that was never sent."""
    project_id = await project_factory()
    broken = payload(project_id, uuid.uuid4())
    broken["trace_id"] = "not-a-uuid"
    await publish(redis_client, broken)

    await fb.process_stream(await fb.read_batch())
    assert [d["reason"] for d in await dlq(redis_client)] == ["undecodable_payload"]
    assert await redis_client.zcard(FEEDBACK_DEFERRED_KEY) == 0


async def test_a_bad_message_does_not_block_a_good_one(fb, redis_client, project_factory):
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await redis_client.xadd(FEEDBACK_STREAM, {"payload": "{not json"})
    await publish(redis_client, payload(project_id, trace_id, comment="fine"))

    assert await fb.process_stream(await fb.read_batch()) == 1
    assert [r["comment"] for r in await rows()] == ["fine"]


async def test_neutral_rating_is_refused_at_the_worker_too(fb, redis_client, project_factory):
    """Defence in depth: the API rejects rating 0, but a hand-written stream
    entry must not create feedback that no filter surfaces."""
    project_id = await project_factory()
    trace_id = await insert_trace(project_id)
    await publish(redis_client, payload(project_id, trace_id, rating=0))

    await fb.process_stream(await fb.read_batch())
    assert await rows() == []
    assert [d["reason"] for d in await dlq(redis_client)] == ["undecodable_payload"]


async def test_naive_timestamp_is_read_as_utc(project_factory):
    """A naive `enqueued_at` would make every age subtraction raise, which would
    turn a clock-format bug into total feedback loss."""
    event = FeedbackEvent.parse(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "rating": -1,
            "enqueued_at": "2020-01-01T12:00:00",
        }
    )
    assert event is not None
    assert event.enqueued_at.tzinfo is not None
    # The point is that the subtraction works at all — naive minus aware raises.
    assert event.age_s(now()) > 0


async def test_unparseable_timestamp_is_treated_as_new(project_factory):
    """Treating it as expired would silently discard real feedback."""
    event = FeedbackEvent.parse(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "rating": 1,
            "enqueued_at": "yesterday-ish",
        }
    )
    assert event is not None
    assert event.age_s(now()) < 5
