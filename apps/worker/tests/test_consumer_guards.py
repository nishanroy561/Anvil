"""The two mandatory upsert guards (ADR-0003, checklist rows 1–2).

These are the tests that would have to fail before a cross-tenant overwrite or
a regressed trace could reach the database.
"""

import uuid

from sqlalchemy import text

from anvil_worker.db import engine

from .conftest import requires_services
from .factories import make_span, make_trace, utc

pytestmark = requires_services


async def _trace_row(trace_id: str) -> dict | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id, project_id, name, status, ended_at, latency_ms "
                    "FROM traces WHERE id = :id"
                ),
                {"id": uuid.UUID(trace_id)},
            )
        ).first()
    return dict(row._mapping) if row else None


async def _span_count(trace_id: str) -> int:
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text("SELECT count(*) FROM spans WHERE trace_id = :id"),
                {"id": uuid.UUID(trace_id)},
            )
        ).scalar_one()


async def test_round_trip_persists_the_whole_tree(consumer, project_factory):
    """Also the column-drift guard for ADR-0014: the worker declares these
    tables itself, so a renamed column surfaces here."""
    project_id = await project_factory()
    root = make_span(name="retrieve", type="retrieval", tokens_in=None, tokens_out=None)
    child = make_span(name="generate", parent_span_id=root["id"])
    trace = make_trace(project_id, spans=[root, child])

    await consumer.persist([trace])

    stored = await _trace_row(trace["id"])
    assert stored["name"] == "rag query"
    assert stored["status"] == "ok"
    assert str(stored["project_id"]) == str(project_id)
    assert await _span_count(trace["id"]) == 2

    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, parent_span_id, name FROM spans WHERE trace_id = :id"),
                {"id": uuid.UUID(trace["id"])},
            )
        ).all()
    by_name = {r.name: r for r in rows}
    assert by_name["generate"].parent_span_id == uuid.UUID(root["id"])
    assert by_name["retrieve"].parent_span_id is None


async def test_redelivery_is_idempotent(consumer, project_factory):
    """At-least-once delivery means this batch *will* arrive twice."""
    project_id = await project_factory()
    trace = make_trace(project_id, spans=[make_span(), make_span()])

    await consumer.persist([dict(trace)])
    await consumer.persist([dict(trace)])

    assert await _span_count(trace["id"]) == 2
    async with engine.begin() as conn:
        total = (await conn.execute(text("SELECT count(*) FROM traces"))).scalar_one()
    assert total == 1


async def test_tenant_guard_blocks_cross_tenant_overwrite(consumer, project_factory):
    """Trace IDs are client-supplied. Submitting a victim's trace ID must not
    let an attacker overwrite — or even read — their row (checklist row 1)."""
    victim_project = await project_factory("Victim")
    attacker_project = await project_factory("Attacker")

    victim_trace = make_trace(victim_project, name="victim trace")
    await consumer.persist([victim_trace])

    # Same trace ID, attacker's project, different content.
    forgery = make_trace(
        attacker_project, trace_id=victim_trace["id"], name="overwritten by attacker"
    )
    await consumer.persist([forgery])

    stored = await _trace_row(victim_trace["id"])
    assert stored["name"] == "victim trace"
    assert str(stored["project_id"]) == str(victim_project)

    # The forged spans must not have landed on the victim's trace either.
    assert await _span_count(victim_trace["id"]) == len(victim_trace["spans"])


async def test_monotonic_guard_keeps_completion_over_late_in_progress(
    consumer, project_factory
):
    """With a consumer group these events can be processed out of order by
    different workers; a completed trace must never regress (checklist row 2)."""
    project_id = await project_factory()
    trace_id = str(uuid.uuid4())

    completed = make_trace(
        project_id, trace_id=trace_id, name="done", status="ok", ended_at=utc(5)
    )
    await consumer.persist([completed])

    late_start = make_trace(
        project_id, trace_id=trace_id, name="starting", status="in_progress", ended_at=None
    )
    await consumer.persist([late_start])

    stored = await _trace_row(trace_id)
    assert stored["status"] == "ok"
    assert stored["name"] == "done"
    assert stored["ended_at"] is not None


async def test_completion_still_applies_to_an_in_progress_trace(consumer, project_factory):
    """The guard blocks regression, not progress."""
    project_id = await project_factory()
    trace_id = str(uuid.uuid4())

    await consumer.persist(
        [make_trace(project_id, trace_id=trace_id, status="in_progress", ended_at=None)]
    )
    await consumer.persist(
        [make_trace(project_id, trace_id=trace_id, name="finished", status="ok", ended_at=utc(3))]
    )

    stored = await _trace_row(trace_id)
    assert stored["status"] == "ok"
    assert stored["name"] == "finished"
    assert stored["latency_ms"] is not None


async def test_batch_carrying_both_events_for_one_trace_keeps_the_completion(
    consumer, project_factory
):
    """Postgres refuses to update one row twice in a single upsert, so the
    batch is merged before it is sent — completion wins."""
    project_id = await project_factory()
    trace_id = str(uuid.uuid4())

    await consumer.persist(
        [
            make_trace(project_id, trace_id=trace_id, status="in_progress", ended_at=None),
            make_trace(project_id, trace_id=trace_id, name="done", status="ok", ended_at=utc(4)),
        ]
    )

    stored = await _trace_row(trace_id)
    assert stored["status"] == "ok"
    assert stored["ended_at"] is not None


async def test_full_size_batch_stays_under_the_bind_parameter_limit(
    consumer, project_factory
):
    """Regression: Postgres caps a statement at 32767 bind parameters.

    A full batch (500 traces x ~8 spans) blew past that and crash-looped the
    consumer — invisible to small-batch tests, which is why this one is sized
    like production traffic rather than like a unit test.
    """
    project_id = await project_factory()
    traces = [
        make_trace(project_id, spans=[make_span() for _ in range(8)]) for _ in range(500)
    ]

    written, spans_written = await consumer.persist(traces)

    assert written == 500
    assert spans_written == 4000


async def test_one_blocked_trace_does_not_fail_the_rest_of_the_batch(
    consumer, project_factory
):
    """A tenant-guard block is counted and logged, never an error — otherwise
    one hostile trace could deny ingestion to everything batched with it."""
    victim_project = await project_factory("Victim")
    attacker_project = await project_factory("Attacker")

    victim_trace = make_trace(victim_project, name="victim trace")
    await consumer.persist([victim_trace])

    legit = make_trace(attacker_project, name="legitimate")
    forgery = make_trace(attacker_project, trace_id=victim_trace["id"], name="forged")
    written, _ = await consumer.persist([forgery, legit])

    assert written == 1
    assert (await _trace_row(legit["id"]))["name"] == "legitimate"
    assert (await _trace_row(victim_trace["id"]))["name"] == "victim trace"
