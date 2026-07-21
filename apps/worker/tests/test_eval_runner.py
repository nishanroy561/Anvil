"""Eval runner tests (plan/evaluation.md §3).

The scenarios that matter are the ones that cost money or lose work: resume
after a kill, cancellation, one bad item not sinking a run, and the
grader-error aggregation rule. LLMs are faked throughout — the suite is
keyless.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import insert, select, text

from anvil_worker.db import engine
from anvil_worker.eval_runner import EvalRunner, summarize
from anvil_worker.tables import dataset_items, datasets, eval_results, eval_runs

from .conftest import requires_services

pytestmark = requires_services


class FakeModel:
    """Scripted LLM. `fail_on` makes specific prompts explode."""

    def __init__(self, reply: str = "an answer", fail_on: str | None = None):
        self.reply = reply
        self.fail_on = fail_on
        self.calls = 0

    async def ainvoke(self, prompt: str):
        self.calls += 1
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("provider exploded")

        class Reply:
            content = self.reply
            usage_metadata = {"input_tokens": 10, "output_tokens": 5}

        return Reply()


async def _make_dataset(project_id: uuid.UUID, n: int = 10) -> tuple[uuid.UUID, list[uuid.UUID]]:
    dataset_id = uuid.uuid4()
    item_ids = [uuid.uuid4() for _ in range(n)]
    async with engine.begin() as conn:
        await conn.execute(
            insert(datasets).values(
                id=dataset_id, project_id=project_id, name="ds", description=None
            )
        )
        await conn.execute(
            insert(dataset_items).values(
                [
                    {
                        "id": item_id,
                        "dataset_id": dataset_id,
                        "input": {"question": f"q{i}"},
                        "expected_output": "an answer",
                        "metadata": None,
                        "source_trace_id": None,
                    }
                    for i, item_id in enumerate(item_ids)
                ]
            )
        )
    return dataset_id, item_ids


async def _make_run(project_id: uuid.UUID, dataset_id: uuid.UUID, graders=None, target=None):
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            insert(eval_runs).values(
                id=run_id,
                project_id=project_id,
                dataset_id=dataset_id,
                name="run",
                status="queued",
                target_config=target or {"mode": "generate", "model": "fake-model", "prompt": "{{question}}"},
                grader_config={"graders": graders or [{"name": "exact_match", "config": {}}]},
                progress=0,
                total=0,
            )
        )
    return run_id


async def _run_row(run_id: uuid.UUID) -> dict:
    async with engine.begin() as conn:
        row = (await conn.execute(select(eval_runs).where(eval_runs.c.id == run_id))).mappings().one()
    return dict(row)


async def _results(run_id: uuid.UUID) -> list[dict]:
    async with engine.begin() as conn:
        rows = (
            await conn.execute(select(eval_results).where(eval_results.c.run_id == run_id))
        ).mappings()
    return [dict(r) for r in rows]


async def test_end_to_end_run_scores_every_item_and_summarizes(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, item_ids = await _make_dataset(project_id, n=10)
    run_id = await _make_run(project_id, dataset_id)

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    await runner.execute(run_id)

    run = await _run_row(run_id)
    assert run["status"] == "completed"
    assert run["progress"] == 10
    assert run["total"] == 10
    assert run["completed_at"] is not None

    rows = await _results(run_id)
    assert len(rows) == 10
    assert {r["item_id"] for r in rows} == set(item_ids)
    # Every item matched the expected output, so the grader is perfect.
    assert run["summary"]["graders"]["exact_match"]["mean"] == 1.0
    assert run["summary"]["graders"]["exact_match"]["errored_items"] == 0
    assert run["summary"]["total_items"] == 10


async def test_item_snapshot_is_frozen_at_run_start(project_factory, redis_client):
    """Items added mid-run must not change what the run measured."""
    project_id = await project_factory()
    dataset_id, item_ids = await _make_dataset(project_id, n=3)
    run_id = await _make_run(project_id, dataset_id)

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    run = await runner._claim(run_id)
    assert len(run["item_ids"]) == 3

    # Dataset grows after the snapshot was taken.
    async with engine.begin() as conn:
        await conn.execute(
            insert(dataset_items).values(
                id=uuid.uuid4(),
                dataset_id=dataset_id,
                input={"question": "late arrival"},
                expected_output="x",
                metadata=None,
                source_trace_id=None,
            )
        )

    await runner.execute(run_id)
    rows = await _results(run_id)
    assert len(rows) == 3
    assert {r["item_id"] for r in rows} == set(item_ids)


async def test_one_failing_item_does_not_fail_the_run(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=5)
    run_id = await _make_run(project_id, dataset_id)

    # The target model explodes on exactly one item's prompt.
    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel(fail_on="q3"))
    await runner.execute(run_id)

    run = await _run_row(run_id)
    assert run["status"] == "completed"

    rows = await _results(run_id)
    assert len(rows) == 5
    errored = [r for r in rows if r["error"]]
    assert len(errored) == 1
    assert "provider exploded" in errored[0]["error"]
    assert run["summary"]["failed_items"] == 1


async def test_killing_the_runner_mid_run_resumes_without_duplicates(project_factory, redis_client):
    """The unique (run_id, item_id) index is what makes resume safe."""
    project_id = await project_factory()
    dataset_id, item_ids = await _make_dataset(project_id, n=6)
    run_id = await _make_run(project_id, dataset_id)

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())

    # Simulate a crash after the first two items landed.
    run = await runner._claim(run_id)
    items = await runner._load_items(run)
    from anvil_worker.eval_runner import _run_item

    partial = [await _run_item(run, item, runner.model_factory) for item in items[:2]]
    await runner._persist(run_id, partial)
    assert len(await _results(run_id)) == 2

    # Live progress reflects what actually landed, not what this process wrote.
    assert (await _run_row(run_id))["progress"] == 2

    # A fresh runner picks the run back up.
    resumed = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    await resumed.execute(run_id)

    rows = await _results(run_id)
    assert len(rows) == 6, "resume duplicated or dropped items"
    assert {r["item_id"] for r in rows} == set(item_ids)
    assert (await _run_row(run_id))["status"] == "completed"


async def test_cancellation_stops_the_run_between_items(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=20)
    run_id = await _make_run(project_id, dataset_id)

    async with engine.begin() as conn:
        await conn.execute(text("UPDATE eval_runs SET status = 'cancelled' WHERE id = :i"), {"i": run_id})

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    await runner.execute(run_id)

    run = await _run_row(run_id)
    # A cancelled run is never silently completed.
    assert run["status"] == "cancelled"
    assert run["summary"] is None


async def test_rescore_mode_needs_no_model(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=3)
    run_id = await _make_run(
        project_id, dataset_id, target={"mode": "rescore"}
    )

    # No model_factory at all — rescore grades the stored output.
    runner = EvalRunner(redis_client, model_factory=None)
    await runner.execute(run_id)

    run = await _run_row(run_id)
    assert run["status"] == "completed"
    assert run["summary"]["graders"]["exact_match"]["mean"] == 1.0


async def test_orphaned_run_is_reclaimed_after_its_heartbeat_goes_stale(
    project_factory, redis_client
):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=3)
    run_id = await _make_run(project_id, dataset_id)

    # A run left "running" with an ancient heartbeat is what a killed worker leaves.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE eval_runs SET status='running', updated_at = now() - interval '1 hour' "
                "WHERE id = :i"
            ),
            {"i": run_id},
        )

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    await runner._resume_orphans()

    assert (await _run_row(run_id))["status"] == "completed"


async def test_fresh_run_is_not_reclaimed(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=3)
    run_id = await _make_run(project_id, dataset_id)

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE eval_runs SET status='running', updated_at = now() WHERE id = :i"),
            {"i": run_id},
        )

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    await runner._resume_orphans()

    # Still running — another worker's live run must not be stolen.
    assert (await _run_row(run_id))["status"] == "running"


async def test_job_consumed_from_the_stream(project_factory, redis_client):
    project_id = await project_factory()
    dataset_id, _ = await _make_dataset(project_id, n=2)
    run_id = await _make_run(project_id, dataset_id)

    from anvil_worker.streams import EVAL_JOBS_STREAM

    await redis_client.xadd(EVAL_JOBS_STREAM, {"run_id": str(run_id)})

    runner = EvalRunner(redis_client, model_factory=lambda _m: FakeModel())
    task = asyncio.create_task(runner.run())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if (await _run_row(run_id))["status"] == "completed":
            break
    runner.stop()
    task.cancel()

    assert (await _run_row(run_id))["status"] == "completed"


# ---- summarize (pure) -------------------------------------------------------


def test_grader_errored_item_is_excluded_from_that_graders_mean():
    """The aggregation rule: a broken grader must not look like a regression."""
    rows = [
        {"scores": {"acc": {"score": 1.0}}, "latency_ms": 10, "cost_usd": None, "error": None},
        {"scores": {"acc": {"score": 1.0}}, "latency_ms": 10, "cost_usd": None, "error": None},
        {"scores": {"acc": {"error": "judge timed out"}}, "latency_ms": 10, "cost_usd": None, "error": None},
    ]
    summary = summarize(rows, [{"name": "acc", "config": {"threshold": 0.5}}])
    acc = summary["graders"]["acc"]
    assert acc["mean"] == 1.0, "errored item was scored 0 instead of excluded"
    assert acc["scored_items"] == 2
    assert acc["errored_items"] == 1


def test_summary_reports_pass_rate_against_the_configured_threshold():
    rows = [
        {"scores": {"j": {"score": 0.9}}, "latency_ms": 1, "cost_usd": None, "error": None},
        {"scores": {"j": {"score": 0.6}}, "latency_ms": 1, "cost_usd": None, "error": None},
    ]
    summary = summarize(rows, [{"name": "j", "config": {"threshold": 0.8}}])
    assert summary["graders"]["j"]["pass_rate"] == 0.5
    assert summary["graders"]["j"]["mean"] == pytest.approx(0.75)


def test_summary_of_an_all_errored_grader_is_null_not_zero():
    rows = [{"scores": {"j": {"error": "boom"}}, "latency_ms": 1, "cost_usd": None, "error": None}]
    summary = summarize(rows, [{"name": "j", "config": {}}])
    assert summary["graders"]["j"]["mean"] is None
    assert summary["graders"]["j"]["errored_items"] == 1
