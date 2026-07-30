"""Run-comparison endpoint (plan/evaluation.md §4, ADR-0008).

The statistics themselves are tested against hand-computed fixtures in
`test_analysis.py`; this file covers the endpoint around them — tenancy,
pairing across the stored snapshots, and the refusals.
"""

import json
import uuid

from sqlalchemy import text

from anvil_api.db import engine

from .conftest import requires_services

pytestmark = requires_services


async def make_dataset(tenant, name="Golden set"):
    res = await tenant.client.post(f"/v1/projects/{tenant.project_id}/datasets", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


async def seed_run(
    project_id: str,
    dataset_id: str,
    item_ids: list[uuid.UUID],
    scores: list[float | None],
    *,
    name: str = "run",
    grader: str = "accuracy",
    latency_ms: int = 100,
    cost_usd: float = 0.001,
    tautological: bool = False,
) -> str:
    """A completed run with one result per item.

    Written straight to the database: the worker executes runs and does not run
    in the API suite, so the rows it would have produced are seeded here.
    `None` in `scores` records a grader error for that item.
    """
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO eval_runs (id, project_id, dataset_id, name, status, "
                "target_config, grader_config, item_ids, progress, total, created_at) "
                "VALUES (:id, :pid, :did, :name, 'completed', CAST(:target AS jsonb), "
                "CAST(:graders AS jsonb), CAST(:items AS jsonb), :n, :n, now())"
            ),
            {
                "id": run_id,
                "pid": project_id,
                "did": dataset_id,
                "name": name,
                "target": json.dumps({"mode": "rescore"}),
                "graders": json.dumps({"graders": [{"name": grader, "config": {}}]}),
                "items": json.dumps([str(i) for i in item_ids]),
                "n": len(item_ids),
            },
        )
        for item_id, score in zip(item_ids, scores):
            entry = {"error": "grader timed out"} if score is None else {"score": score}
            if score is not None and tautological:
                entry["tautological"] = True
            await conn.execute(
                text(
                    "INSERT INTO eval_results (id, run_id, item_id, output, scores, "
                    "latency_ms, cost_usd, created_at) VALUES (:id, :rid, :iid, "
                    "CAST(:out AS jsonb), CAST(:scores AS jsonb), :lat, :cost, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "rid": run_id,
                    "iid": item_id,
                    "out": json.dumps({"value": "x"}),
                    "scores": json.dumps({grader: entry}),
                    "lat": latency_ms,
                    "cost": cost_usd,
                },
            )
    return str(run_id)


async def make_items(tenant, dataset_id: str, n: int) -> list[uuid.UUID]:
    res = await tenant.client.post(
        f"/v1/datasets/{dataset_id}/items",
        json={"items": [{"input": {"q": f"q{i}"}, "expected_output": "a"} for i in range(n)]},
    )
    assert res.status_code == 201, res.text
    return [uuid.UUID(i["id"]) for i in res.json()]


# ---- the endpoint -----------------------------------------------------------


async def test_compare_reports_a_significant_improvement(tenant):
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 40)
    a = await seed_run(tenant.project_id, dataset["id"], items, [0.0] * 40, name="v1")
    b = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 40, name="v2")

    res = await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["compared_items"] == 40
    assert body["partial"] is False
    assert body["run_a"]["name"] == "v1"
    assert body["run_b"]["name"] == "v2"

    grader = body["graders"][0]
    assert grader["grader"] == "accuracy"
    assert grader["mean_a"] == 0.0
    assert grader["mean_b"] == 1.0
    assert grader["delta"] == 1.0
    assert grader["verdict"] == "improved"
    assert grader["significant"] is True
    assert grader["ci"][0] > 0


async def test_compare_of_a_small_run_pair_is_inconclusive(tenant):
    """The Phase 6 acceptance's second half: small n must decline to call it
    however large the apparent shift (ADR-0008)."""
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 5)
    a = await seed_run(tenant.project_id, dataset["id"], items, [0.0] * 5)
    b = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 5)

    body = (await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).json()
    grader = body["graders"][0]

    assert grader["delta"] == 1.0
    assert grader["verdict"] == "inconclusive"
    assert grader["reason"] == "insufficient_data"
    assert grader["significant"] is False


async def test_compare_pairs_on_the_snapshot_intersection(tenant):
    """The dataset grew between the two runs. Only the shared items pair, and
    the response says so — 'compared on 8 of 12'."""
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 12)
    shared, extra = items[:8], items[8:]

    a = await seed_run(tenant.project_id, dataset["id"], shared, [0.0] * 8)
    b = await seed_run(tenant.project_id, dataset["id"], shared + extra, [1.0] * 12)

    body = (await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).json()

    assert body["compared_items"] == 8
    assert body["partial"] is True
    assert body["run_a"]["total_items"] == 8
    assert body["run_b"]["total_items"] == 12
    assert body["graders"][0]["n"] == 8


async def test_compare_drops_pairs_where_a_grader_errored(tenant):
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 10)
    a = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 10)
    b = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 7 + [None] * 3)

    body = (await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).json()

    assert body["graders"][0]["n"] == 7, "errored pairs were counted"
    assert body["graders"][0]["mean_b"] == 1.0, "errors were averaged in as zeros"


async def test_compare_refuses_a_tautological_pair(tenant):
    """Both runs graded output against itself, so the zero delta means nothing
    (ADR-0018) and must not read as 'no regression'."""
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 40)
    a = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 40, tautological=True)
    b = await seed_run(tenant.project_id, dataset["id"], items, [1.0] * 40, tautological=True)

    grader = (await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).json()["graders"][0]

    assert grader["verdict"] == "inconclusive"
    assert grader["reason"] == "tautological"
    assert grader["tautological_pairs"] == 40


async def test_compare_reports_latency_and_cost(tenant):
    dataset = await make_dataset(tenant)
    items = await make_items(tenant, dataset["id"], 5)
    a = await seed_run(
        tenant.project_id, dataset["id"], items, [1.0] * 5, latency_ms=100, cost_usd=0.001
    )
    b = await seed_run(
        tenant.project_id, dataset["id"], items, [1.0] * 5, latency_ms=250, cost_usd=0.004
    )

    body = (await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).json()

    assert body["latency"]["a"] == 100
    assert body["latency"]["b"] == 250
    assert body["latency"]["delta"] == 150
    assert body["cost"]["delta"] > 0


# ---- refusals and tenancy ---------------------------------------------------


async def test_comparing_runs_over_different_datasets_is_refused(tenant):
    """Different datasets have different items, so there is nothing to pair."""
    first, second = await make_dataset(tenant), await make_dataset(tenant, name="Other")
    items_a = await make_items(tenant, first["id"], 3)
    items_b = await make_items(tenant, second["id"], 3)
    a = await seed_run(tenant.project_id, first["id"], items_a, [1.0] * 3)
    b = await seed_run(tenant.project_id, second["id"], items_b, [1.0] * 3)

    res = await tenant.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "datasets_differ"


async def test_compare_is_not_swallowed_by_the_run_detail_route(tenant):
    """`/eval-runs/compare` is declared before `/eval-runs/{run_id}`; the other
    order parses 'compare' as a run id and 422s."""
    res = await tenant.client.get(f"/v1/eval-runs/compare?a={uuid.uuid4()}&b={uuid.uuid4()}")
    assert res.status_code == 404, "route fell through to the detail handler"


async def test_compare_404s_across_tenants(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset(mine)
    items = await make_items(mine, dataset["id"], 3)
    a = await seed_run(mine.project_id, dataset["id"], items, [1.0] * 3)
    b = await seed_run(mine.project_id, dataset["id"], items, [0.0] * 3)

    assert (await theirs.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).status_code == 404
    assert (await mine.client.get(f"/v1/eval-runs/compare?a={a}&b={b}")).status_code == 200


async def test_compare_requires_both_run_ids(tenant):
    assert (await tenant.client.get(f"/v1/eval-runs/compare?a={uuid.uuid4()}")).status_code == 422
