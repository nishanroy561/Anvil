"""Stream mechanics: ack/delete, reclaim, and the dead-letter queue.

These cover checklist row 4 (a poison message must not crash-loop the worker)
and the reclaim behaviour that the Phase-2 acceptance criterion — killing the
worker mid-run without losing a trace — depends on.
"""

import json
import uuid

from sqlalchemy import text

from anvil_worker.db import engine
from anvil_worker.streams import DLQ_STREAM, PERSISTERS_GROUP, TRACES_STREAM

from .conftest import requires_services
from .factories import make_trace

pytestmark = requires_services


async def _publish(redis_client, trace: dict) -> str:
    return await redis_client.xadd(TRACES_STREAM, {"payload": json.dumps(trace)})


async def _pending_count(redis_client) -> int:
    summary = await redis_client.xpending(TRACES_STREAM, PERSISTERS_GROUP)
    return summary["pending"] if summary else 0


async def test_read_persist_ack_clears_the_stream(consumer, redis_client, project_factory):
    project_id = await project_factory()
    trace = make_trace(project_id)
    await _publish(redis_client, trace)

    await consumer.process(await consumer.read_batch())

    async with engine.begin() as conn:
        stored = (
            await conn.execute(
                text("SELECT count(*) FROM traces WHERE id = :id"),
                {"id": uuid.UUID(trace["id"])},
            )
        ).scalar_one()
    assert stored == 1
    # Acked *and* deleted: nothing pending, nothing left occupying the stream.
    assert await _pending_count(redis_client) == 0
    assert await redis_client.xlen(TRACES_STREAM) == 0


async def test_undecodable_payload_goes_straight_to_the_dlq(consumer, redis_client):
    """No amount of retrying fixes malformed JSON, so it skips the retry path."""
    await redis_client.xadd(TRACES_STREAM, {"payload": "{not json at all"})

    await consumer.process(await consumer.read_batch())

    dlq = await redis_client.xrange(DLQ_STREAM)
    assert len(dlq) == 1
    assert dlq[0][1]["reason"] == "undecodable_payload"
    assert await _pending_count(redis_client) == 0


async def test_poison_message_is_dead_lettered_after_five_deliveries(
    consumer, redis_client, monkeypatch, project_factory
):
    """A message that has already survived `max_deliveries` attempts is set
    aside rather than handed to yet another consumer to die on."""
    project_id = await project_factory()
    trace = make_trace(project_id)
    message_id = await _publish(redis_client, trace)

    # Deliver it repeatedly without acking, as a crash-looping replica would.
    await consumer.read_batch()
    for _ in range(4):
        await redis_client.xclaim(
            TRACES_STREAM,
            PERSISTERS_GROUP,
            "another-consumer",
            min_idle_time=0,
            message_ids=[message_id],
        )

    monkeypatch.setattr(consumer.settings, "reclaim_idle_ms", 0)
    reclaimed = await consumer.reclaim_pending()

    assert reclaimed == []  # not handed back for processing
    dlq = await redis_client.xrange(DLQ_STREAM)
    assert len(dlq) == 1
    assert dlq[0][1]["reason"] == "max_deliveries_exceeded"
    # Payload preserved so it can be replayed once the bug is fixed.
    assert json.loads(dlq[0][1]["payload"])["id"] == trace["id"]
    assert await _pending_count(redis_client) == 0


async def test_poison_message_does_not_block_healthy_traffic(
    consumer, redis_client, monkeypatch, project_factory
):
    project_id = await project_factory()
    poison_id = await _publish(redis_client, make_trace(project_id))
    await consumer.read_batch()
    for _ in range(5):
        await redis_client.xclaim(
            TRACES_STREAM, PERSISTERS_GROUP, "dying-consumer",
            min_idle_time=0, message_ids=[poison_id],
        )

    healthy = make_trace(project_id, name="healthy trace")
    await _publish(redis_client, healthy)

    monkeypatch.setattr(consumer.settings, "reclaim_idle_ms", 0)
    await consumer.reclaim_pending()
    await consumer.process(await consumer.read_batch())

    async with engine.begin() as conn:
        name = (
            await conn.execute(
                text("SELECT name FROM traces WHERE id = :id"),
                {"id": uuid.UUID(healthy["id"])},
            )
        ).scalar_one()
    assert name == "healthy trace"
    assert await redis_client.xlen(DLQ_STREAM) == 1


async def test_pending_entries_from_a_dead_consumer_are_reclaimed(
    consumer, redis_client, monkeypatch, project_factory
):
    """The mid-run worker kill, in miniature: a consumer takes a message and
    dies before acking. A survivor must pick it up and persist it."""
    project_id = await project_factory()
    trace = make_trace(project_id, name="orphaned by a crash")
    message_id = await _publish(redis_client, trace)

    # A different consumer takes delivery and never acks — i.e. it crashed.
    await redis_client.xreadgroup(
        PERSISTERS_GROUP, "doomed-consumer", {TRACES_STREAM: ">"}, count=10
    )
    assert await _pending_count(redis_client) == 1

    monkeypatch.setattr(consumer.settings, "reclaim_idle_ms", 0)
    await consumer.process(await consumer.reclaim_pending())

    async with engine.begin() as conn:
        name = (
            await conn.execute(
                text("SELECT name FROM traces WHERE id = :id"),
                {"id": uuid.UUID(trace["id"])},
            )
        ).scalar_one()
    assert name == "orphaned by a crash"
    assert await _pending_count(redis_client) == 0
    assert message_id is not None
