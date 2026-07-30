"""Retention sweep and usage rollups (Phase 10, plan/retention.md,
checklist rows 23–24, ADR-0022).

The two behaviours that matter most here are both about *not* doing something:
the worker must delete nothing when no window is configured, and a curated
dataset must survive the deletion of the traces it was built from.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from anvil_worker import retention as ret
from anvil_worker.db import engine

from .conftest import requires_services

pytestmark = requires_services


def now() -> datetime:
    return datetime.now(timezone.utc)


async def insert_traces(
    project_id: uuid.UUID,
    n: int,
    days_ago: float | None = None,
    at: datetime | None = None,
) -> list[uuid.UUID]:
    """`days_ago` is relative to wall clock; `at` pins an absolute timestamp, which
    is what the boundary test needs to be deterministic."""
    ids = [uuid.uuid4() for _ in range(n)]
    started = at if at is not None else now() - timedelta(days=days_ago or 0)
    async with engine.begin() as conn:
        for trace_id in ids:
            await conn.execute(
                text(
                    "INSERT INTO traces (id, project_id, name, status, started_at, "
                    "input, output) VALUES (:id, :pid, 'ask', 'ok', :started, "
                    "CAST(:inp AS jsonb), CAST(:out AS jsonb))"
                ),
                {
                    "id": trace_id,
                    "pid": project_id,
                    "started": started,
                    "inp": '{"question": "how long do refunds take?"}',
                    "out": '"5-7 business days"',
                },
            )
    return ids


async def add_span(trace_id: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
    span_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO spans (id, trace_id, project_id, name, type, status, "
                "started_at) VALUES (:id, :tid, :pid, 'llm', 'llm', 'ok', :started)"
            ),
            {"id": span_id, "tid": trace_id, "pid": project_id, "started": now()},
        )
    return span_id


async def add_feedback(trace_id: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
    fid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feedback (id, project_id, trace_id, rating, created_at) "
                "VALUES (:id, :pid, :tid, -1, :now)"
            ),
            {"id": fid, "pid": project_id, "tid": trace_id, "now": now()},
        )
    return fid


async def promote(dataset_id: uuid.UUID, trace_id: uuid.UUID | None) -> uuid.UUID:
    """A dataset item carrying its own copy of the payloads (ADR-0018).

    `trace_id=None` makes a hand-authored item — one that never came from a trace,
    which is also what `source_trace_id` looks like after retention."""
    item_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO dataset_items (id, dataset_id, input, expected_output, "
                "metadata, source_trace_id) VALUES (:id, :ds, CAST(:inp AS jsonb), "
                "CAST(:exp AS jsonb), CAST(:meta AS jsonb), :tid)"
            ),
            {
                "id": item_id,
                "ds": dataset_id,
                "inp": '{"question": "how long do refunds take?"}',
                "exp": '"5-7 business days"',
                "meta": '{"recorded_output": "5-7 business days"}',
                "tid": trace_id,
            },
        )
    return item_id


async def make_dataset(project_id: uuid.UUID, name: str = "golden") -> uuid.UUID:
    dataset_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO datasets (id, project_id, name, created_at) "
                "VALUES (:id, :pid, :name, :now)"
            ),
            {"id": dataset_id, "pid": project_id, "name": name, "now": now()},
        )
    return dataset_id


async def scalar(sql: str, **params):
    async with engine.begin() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def trace_count() -> int:
    return await scalar("SELECT count(*) FROM traces")


# ---- the default: delete nothing --------------------------------------------


async def test_unset_window_deletes_nothing(project_factory, monkeypatch):
    """The whole safety story. An operator who has not asked for deletion must
    never get deletion just because the worker started."""
    project_id = await project_factory()
    await insert_traces(project_id, 5, days_ago=400)
    monkeypatch.setattr(ret.get_settings(), "retention_days", None, raising=False)

    assert await ret.sweep() == 0
    assert await trace_count() == 5


async def test_zero_or_negative_window_is_refused(project_factory, monkeypatch):
    """A 0-day window would delete traffic that is still arriving. Refuse rather
    than interpret — the failure mode of guessing here is total data loss."""
    project_id = await project_factory()
    await insert_traces(project_id, 3, days_ago=400)

    for bad in (0, -1):
        monkeypatch.setattr(ret.get_settings(), "retention_days", bad, raising=False)
        assert await ret.sweep() == 0
    assert await trace_count() == 3


# ---- the sweep --------------------------------------------------------------


async def test_only_out_of_window_traces_are_deleted(project_factory):
    project_id = await project_factory()
    old = await insert_traces(project_id, 4, days_ago=10)
    fresh = await insert_traces(project_id, 3, days_ago=1)

    assert await ret.sweep(days=7) == 4
    assert await trace_count() == 3
    surviving = await scalar(
        "SELECT count(*) FROM traces WHERE id = ANY(:ids)", ids=list(fresh)
    )
    assert surviving == 3
    assert await scalar("SELECT count(*) FROM traces WHERE id = ANY(:ids)", ids=list(old)) == 0


async def test_a_trace_exactly_on_the_boundary_survives(project_factory):
    """`started_at < cutoff` is strict, so a trace *at* the boundary is inside the
    window. Off by one here quietly deletes a day of traffic.

    `now` is passed explicitly rather than left to the wall clock: with
    `days_ago=7` the trace's timestamp and the sweep's cutoff are computed
    milliseconds apart, so "exactly on the boundary" is unreachable and the test
    would assert whichever side the scheduler happened to land on."""
    project_id = await project_factory()
    boundary = now() - timedelta(days=7)
    on_boundary = await insert_traces(project_id, 1, at=boundary)
    await insert_traces(project_id, 1, at=boundary - timedelta(seconds=1))

    # cutoff(7, now=boundary + 7d) == boundary exactly.
    assert await ret.sweep(days=7, now=boundary + timedelta(days=7)) == 1
    assert await scalar(
        "SELECT count(*) FROM traces WHERE id = ANY(:ids)", ids=list(on_boundary)
    ) == 1
    assert await trace_count() == 1


async def test_spans_and_feedback_cascade(project_factory):
    project_id = await project_factory()
    (trace_id,) = await insert_traces(project_id, 1, days_ago=90)
    await add_span(trace_id, project_id)
    await add_feedback(trace_id, project_id)

    assert await ret.sweep(days=30) == 1
    assert await scalar("SELECT count(*) FROM spans") == 0
    assert await scalar("SELECT count(*) FROM feedback") == 0


async def test_batching_holds_over_more_than_one_batch(project_factory, monkeypatch):
    """Checklist row 23: never one giant DELETE. With a batch size of 3 and 10
    expired traces the sweep must take several statements and still finish."""
    project_id = await project_factory()
    await insert_traces(project_id, 10, days_ago=60)
    monkeypatch.setattr(ret.get_settings(), "retention_batch_size", 3, raising=False)
    monkeypatch.setattr(ret.get_settings(), "retention_batch_sleep_s", 0.0, raising=False)

    calls: list[int] = []
    original = ret.delete_batch

    async def counting(before, limit):
        deleted = await original(before, limit)
        calls.append(deleted)
        return deleted

    monkeypatch.setattr(ret, "delete_batch", counting)

    assert await ret.sweep(days=30) == 10
    assert await trace_count() == 0
    # 3 + 3 + 3 + 1, then a zero-row pass that ends the loop.
    assert calls == [3, 3, 3, 1, 0]
    assert max(calls) <= 3, "no statement exceeded the batch size"


async def test_sweep_is_idempotent(project_factory):
    project_id = await project_factory()
    await insert_traces(project_id, 4, days_ago=60)
    assert await ret.sweep(days=30) == 4
    assert await ret.sweep(days=30) == 0


async def test_sweep_spans_every_org(project_factory):
    """A single global window (ADR-0019 left no per-org plans), so the sweep is
    not org-scoped. Pinned because the pre-ADR-0019 spec said otherwise."""
    a = await project_factory("Org A")
    b = await project_factory("Org B")
    await insert_traces(a, 2, days_ago=60)
    await insert_traces(b, 3, days_ago=60)

    assert await ret.sweep(days=30) == 5
    assert await trace_count() == 0


# ---- datasets survive (checklist row 24) ------------------------------------


async def test_promoted_items_survive_their_source_trace(project_factory):
    """The reason promotion copies payloads instead of referencing them. After
    retention the item keeps its input, its reference output and the recorded
    output — only the back-link goes null."""
    project_id = await project_factory()
    (trace_id,) = await insert_traces(project_id, 1, days_ago=90)
    dataset_id = await make_dataset(project_id)
    item_id = await promote(dataset_id, trace_id)

    assert await ret.sweep(days=30) == 1

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT input, expected_output, metadata, source_trace_id "
                    "FROM dataset_items WHERE id = :id"
                ),
                {"id": item_id},
            )
        ).mappings().one()

    assert row["source_trace_id"] is None, "the link is SET NULL, not the row deleted"
    assert row["input"]["question"] == "how long do refunds take?"
    assert row["expected_output"] == "5-7 business days"
    assert row["metadata"]["recorded_output"] == "5-7 business days"
    assert await scalar("SELECT count(*) FROM dataset_items") == 1


# ---- usage rollups (ADR-0022) ----------------------------------------------


async def test_rollups_count_traces_per_org_month(project_factory):
    a = await project_factory("Org A")
    b = await project_factory("Org B")
    await insert_traces(a, 3, days_ago=1)
    await insert_traces(b, 2, days_ago=1)

    assert await ret.recompute_usage() >= 2
    async with engine.begin() as conn:
        rows = {
            (r["org_id"], r["month"]): r["traces_ingested"]
            for r in (
                await conn.execute(text("SELECT * FROM org_usage"))
            ).mappings()
        }
    assert sorted(rows.values()) == [2, 3]


async def test_rollups_are_idempotent(project_factory):
    project_id = await project_factory()
    await insert_traces(project_id, 4, days_ago=1)
    await ret.recompute_usage()
    await ret.recompute_usage()

    assert await scalar("SELECT count(*) FROM org_usage") == 1
    assert await scalar("SELECT traces_ingested FROM org_usage") == 4


async def test_retention_cannot_rewrite_a_month_as_zero(project_factory):
    """ADR-0022, and the bug it exists to prevent: recompute-and-overwrite would
    report the operator's busiest month as their quietest, one window later."""
    project_id = await project_factory()
    await insert_traces(project_id, 6, days_ago=90)
    await ret.recompute_usage()
    assert await scalar("SELECT traces_ingested FROM org_usage") == 6

    # The traces fall out of the window and are deleted...
    assert await ret.sweep(days=30) == 6
    assert await trace_count() == 0

    # ...and the next daily pass must not revise the month down.
    await ret.recompute_usage()
    assert await scalar("SELECT traces_ingested FROM org_usage") == 6


async def test_a_growing_month_still_climbs(project_factory):
    """GREATEST must not freeze the current month — it is a no-op while a count
    is rising, which is the only reason the rollup stays useful."""
    project_id = await project_factory()
    await insert_traces(project_id, 2, days_ago=1)
    await ret.recompute_usage()
    assert await scalar("SELECT traces_ingested FROM org_usage") == 2

    await insert_traces(project_id, 3, days_ago=1)
    await ret.recompute_usage()
    assert await scalar("SELECT traces_ingested FROM org_usage") == 5


async def test_storage_bytes_is_left_null(project_factory):
    """Per-org bytes are not honestly attributable, so the column stays null
    rather than carrying a share-of-rows guess (ADR-0022)."""
    project_id = await project_factory()
    await insert_traces(project_id, 1, days_ago=1)
    await ret.recompute_usage()
    assert await scalar("SELECT storage_bytes FROM org_usage") is None


async def test_rollups_with_no_data_write_nothing(project_factory):
    await project_factory()
    assert await ret.recompute_usage() == 0
    assert await scalar("SELECT count(*) FROM org_usage") == 0


async def test_eval_items_count_graded_results_not_promised_totals(project_factory):
    """A cancelled run promised more items than it graded; the panel is about
    what was actually spent."""
    project_id = await project_factory()
    run_id = uuid.uuid4()
    dataset_id = await make_dataset(project_id)
    # `eval_results.item_id` is a real FK to dataset_items, so the items have to
    # exist — a result cannot reference an item that was never curated.
    items = [await promote(dataset_id, None) for _ in range(2)]
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO eval_runs (id, project_id, dataset_id, name, status, "
                "progress, total, created_at) VALUES (:id, :pid, :ds, 'r', "
                "'cancelled', 2, 50, :now)"
            ),
            {"id": run_id, "pid": project_id, "ds": dataset_id, "now": now()},
        )
        for item_id in items:
            await conn.execute(
                text(
                    "INSERT INTO eval_results (id, run_id, item_id, created_at) "
                    "VALUES (:id, :run, :item, :now)"
                ),
                {"id": uuid.uuid4(), "run": run_id, "item": item_id, "now": now()},
            )

    await ret.recompute_usage()
    assert await scalar("SELECT eval_items FROM org_usage") == 2


# ---- the loop ---------------------------------------------------------------


async def test_tick_rolls_up_even_with_retention_disabled(project_factory, monkeypatch):
    """The usage panel is useful to every operator; deletion is the opt-in half."""
    project_id = await project_factory()
    await insert_traces(project_id, 3, days_ago=500)
    monkeypatch.setattr(ret.get_settings(), "retention_days", None, raising=False)

    deleted, rolled = await ret.Retention().tick()
    assert deleted == 0
    assert rolled == 1
    assert await trace_count() == 3


async def test_a_failing_sweep_does_not_cost_the_rollups(project_factory, monkeypatch):
    project_id = await project_factory()
    await insert_traces(project_id, 2, days_ago=1)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("connection reset mid-sweep")

    monkeypatch.setattr(ret, "sweep", boom)
    deleted, rolled = await ret.Retention().tick()
    assert deleted == 0
    assert rolled == 1


async def test_table_sizes_reports_real_tables():
    sizes = await ret.table_sizes()
    assert sizes["traces"] >= 0
    assert "spans" in sizes and "org_usage" in sizes
