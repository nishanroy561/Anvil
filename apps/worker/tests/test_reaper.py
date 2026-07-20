"""Stale-trace reaper (checklist row 3).

When a customer's app crashes mid-request the completion event never comes, and
the trace would sit `in_progress` forever — indistinguishable from one that is
genuinely still running.
"""

import uuid

from sqlalchemy import text

from anvil_worker.db import engine
from anvil_worker.ingest_consumer import reap_stale_traces

from .conftest import requires_services
from .factories import make_trace, utc

pytestmark = requires_services


async def _insert_in_progress(project_id, trace_id: uuid.UUID, age_minutes: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, started_at) "
                "VALUES (:id, :project_id, 'stuck', 'in_progress', "
                "now() - make_interval(mins => :age))"
            ),
            {"id": trace_id, "project_id": project_id, "age": age_minutes},
        )


async def _status(trace_id: uuid.UUID) -> str:
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text("SELECT status FROM traces WHERE id = :id"), {"id": trace_id}
            )
        ).scalar_one()


async def test_reaper_abandons_only_traces_past_the_threshold(project_factory):
    project_id = await project_factory()
    stale, fresh = uuid.uuid4(), uuid.uuid4()
    await _insert_in_progress(project_id, stale, age_minutes=45)
    await _insert_in_progress(project_id, fresh, age_minutes=5)

    assert await reap_stale_traces() == 1

    assert await _status(stale) == "abandoned"
    # A request that has been running five minutes may be slow, not dead.
    assert await _status(fresh) == "in_progress"


async def test_late_completion_overrides_abandoned(consumer, project_factory):
    """`abandoned` leaves `ended_at` null, so the monotonic guard still admits
    a real completion that turns up afterwards."""
    project_id = await project_factory()
    trace_id = uuid.uuid4()
    await _insert_in_progress(project_id, trace_id, age_minutes=45)
    await reap_stale_traces()
    assert await _status(trace_id) == "abandoned"

    await consumer.persist(
        [
            make_trace(
                project_id,
                trace_id=str(trace_id),
                name="finished after all",
                status="ok",
                ended_at=utc(1),
            )
        ]
    )

    assert await _status(trace_id) == "ok"


async def test_reaper_leaves_completed_traces_alone(consumer, project_factory):
    project_id = await project_factory()
    trace = make_trace(project_id, status="ok")
    await consumer.persist([trace])

    assert await reap_stale_traces() == 0
    assert await _status(uuid.UUID(trace["id"])) == "ok"
