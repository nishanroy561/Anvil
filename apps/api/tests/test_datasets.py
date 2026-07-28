"""Dataset + eval-run API tests (plan/evaluation.md §1, §3).

The load-bearing cases are tenancy (404, never 403), promotion idempotency, and
the spend guards — the guards are the only thing standing between a mistyped
loop and an unbounded judge bill.
"""

import uuid

from sqlalchemy import text

from anvil_api.db import engine

from .conftest import requires_services

pytestmark = requires_services


async def insert_trace(project_id: str, *, input_json: str = '{"q": "why"}', output_json: str = '"because"') -> str:
    trace_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, started_at, input, output, metadata) "
                "VALUES (:id, :pid, 'ask', 'ok', now(), CAST(:inp AS jsonb), CAST(:out AS jsonb), "
                "CAST('{\"env\": \"prod\"}' AS jsonb))"
            ),
            {"id": trace_id, "pid": project_id, "inp": input_json, "out": output_json},
        )
    return str(trace_id)


async def make_dataset(tenant, name="Golden set"):
    res = await tenant.client.post(f"/v1/projects/{tenant.project_id}/datasets", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


async def make_dataset_with_items(tenant, n=3):
    dataset = await make_dataset(tenant)
    await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/items",
        json={"items": [{"input": {"q": f"q{i}"}, "expected_output": "a"} for i in range(n)]},
    )
    return dataset


def run_body(dataset_id, **overrides):
    body = {
        "dataset_id": dataset_id,
        "name": "baseline",
        "target": {"mode": "rescore"},
        "graders": [{"name": "exact_match", "config": {}}],
    }
    body.update(overrides)
    return body


# ---- CRUD -------------------------------------------------------------------


async def test_create_and_list_datasets(tenant):
    created = await make_dataset(tenant)
    assert created["name"] == "Golden set"
    assert created["item_count"] == 0

    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/datasets")
    assert res.status_code == 200
    assert [d["id"] for d in res.json()] == [created["id"]]


async def test_update_and_delete_dataset(tenant):
    dataset = await make_dataset(tenant)

    res = await tenant.client.patch(f"/v1/datasets/{dataset['id']}", json={"name": "Renamed"})
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed"

    assert (await tenant.client.delete(f"/v1/datasets/{dataset['id']}")).status_code == 204
    assert (await tenant.client.get(f"/v1/datasets/{dataset['id']}")).status_code == 404


async def test_dataset_in_another_org_is_404_never_403(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset(mine)
    assert (await theirs.client.get(f"/v1/datasets/{dataset['id']}")).status_code == 404


async def test_unknown_dataset_is_404(tenant):
    assert (await tenant.client.get(f"/v1/datasets/{uuid.uuid4()}")).status_code == 404


# ---- items ------------------------------------------------------------------


async def test_add_items_and_paginate(tenant):
    dataset = await make_dataset(tenant)
    payload = {"items": [{"input": {"q": f"q{i}"}, "expected_output": f"a{i}"} for i in range(5)]}
    res = await tenant.client.post(f"/v1/datasets/{dataset['id']}/items", json=payload)
    assert res.status_code == 201
    assert len(res.json()) == 5

    body = (await tenant.client.get(f"/v1/datasets/{dataset['id']}/items?limit=2")).json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    assert (await tenant.client.get(f"/v1/datasets/{dataset['id']}")).json()["item_count"] == 5


async def test_expected_output_is_editable(tenant):
    """Editable expected outputs are the point of the items table."""
    dataset = await make_dataset(tenant)
    item = (
        await tenant.client.post(
            f"/v1/datasets/{dataset['id']}/items",
            json={"items": [{"input": {"q": "x"}, "expected_output": "wrong"}]},
        )
    ).json()[0]

    res = await tenant.client.patch(
        f"/v1/dataset-items/{item['id']}", json={"expected_output": "right"}
    )
    assert res.status_code == 200
    assert res.json()["expected_output"] == "right"


async def test_item_delete_and_cross_tenant_404(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset(mine)
    item = (
        await mine.client.post(
            f"/v1/datasets/{dataset['id']}/items", json={"items": [{"input": {"q": "x"}}]}
        )
    ).json()[0]

    assert (await theirs.client.delete(f"/v1/dataset-items/{item['id']}")).status_code == 404
    assert (await mine.client.delete(f"/v1/dataset-items/{item['id']}")).status_code == 204


# ---- promotion --------------------------------------------------------------


async def test_promote_copies_trace_payloads_into_items(tenant):
    dataset = await make_dataset(tenant)
    trace_id = await insert_trace(tenant.project_id)

    res = await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]}
    )
    assert res.status_code == 200
    assert res.json() == {"promoted": 1, "skipped": 0}

    items = (await tenant.client.get(f"/v1/datasets/{dataset['id']}/items")).json()["items"]
    assert items[0]["input"] == {"q": "why"}
    assert items[0]["expected_output"] == "because"
    assert items[0]["source_trace_id"] == trace_id
    # Provenance is copied, so it survives retention deleting the trace.
    assert items[0]["metadata"]["promoted_from_trace"] == trace_id
    # The recorded output is kept apart from the reference it will be graded
    # against, or rescore grades a value with itself (ADR-0018).
    assert items[0]["metadata"]["recorded_output"] == "because"
    assert items[0]["metadata"]["expected_output_edited"] is False


async def test_promotion_metadata_cannot_be_shadowed_by_the_customers_own(tenant):
    """A trace whose own metadata carries `recorded_output` must not be able to
    displace the real one — Anvil's keys are written last."""
    trace_id = await insert_trace(tenant.project_id)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE traces SET metadata = CAST(:m AS jsonb) WHERE id = CAST(:id AS uuid)"
            ),
            {"m": '{"recorded_output": "spoofed", "promoted_from_trace": "nonsense"}', "id": trace_id},
        )

    dataset = await make_dataset(tenant)
    await tenant.client.post(f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]})

    meta = (await tenant.client.get(f"/v1/datasets/{dataset['id']}/items")).json()["items"][0][
        "metadata"
    ]
    assert meta["recorded_output"] == "because"
    assert meta["promoted_from_trace"] == trace_id


async def test_editing_the_expected_output_marks_it_edited(tenant):
    """A rescore run reads this to tell a real reference from a copy of the
    output it is about to grade (ADR-0018)."""
    dataset = await make_dataset(tenant)
    trace_id = await insert_trace(tenant.project_id)
    await tenant.client.post(f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]})
    item = (await tenant.client.get(f"/v1/datasets/{dataset['id']}/items")).json()["items"][0]
    assert item["metadata"]["expected_output_edited"] is False

    res = await tenant.client.patch(
        f"/v1/dataset-items/{item['id']}", json={"expected_output": "a better answer"}
    )
    assert res.status_code == 200
    assert res.json()["metadata"]["expected_output_edited"] is True
    # The recorded output is untouched by editing the reference.
    assert res.json()["metadata"]["recorded_output"] == "because"


async def test_editing_input_alone_does_not_mark_the_expected_output_edited(tenant):
    dataset = await make_dataset(tenant)
    trace_id = await insert_trace(tenant.project_id)
    await tenant.client.post(f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]})
    item = (await tenant.client.get(f"/v1/datasets/{dataset['id']}/items")).json()["items"][0]

    res = await tenant.client.patch(f"/v1/dataset-items/{item['id']}", json={"input": {"q": "new"}})
    assert res.json()["metadata"]["expected_output_edited"] is False


async def test_promoting_the_same_trace_twice_is_a_noop(tenant):
    """The button must be safe to double-click."""
    dataset = await make_dataset(tenant)
    trace_id = await insert_trace(tenant.project_id)

    first = await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]}
    )
    second = await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]}
    )
    assert first.json()["promoted"] == 1
    assert second.json() == {"promoted": 0, "skipped": 1}
    assert (await tenant.client.get(f"/v1/datasets/{dataset['id']}")).json()["item_count"] == 1


async def test_promoting_another_tenants_trace_skips_it_rather_than_failing_the_batch(
    tenant_factory,
):
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset(mine)
    ours = await insert_trace(mine.project_id)
    foreign = await insert_trace(theirs.project_id)

    res = await mine.client.post(
        f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [ours, foreign]}
    )
    assert res.status_code == 200
    assert res.json() == {"promoted": 1, "skipped": 1}


# ---- eval runs & spend guards ----------------------------------------------


async def test_create_eval_run_queues_it(tenant):
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["status"] == "queued"
    assert run["total"] == 3
    assert run["progress"] == 0


async def test_empty_dataset_is_rejected(tenant):
    dataset = await make_dataset(tenant)
    res = await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "empty_dataset"


async def test_generate_mode_requires_a_target_model(tenant):
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post(
        "/v1/eval-runs", json=run_body(dataset["id"], target={"mode": "generate"})
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "target_model_required"


async def test_dataset_over_max_eval_items_is_refused(tenant, monkeypatch):
    """Spend guard 1: judge calls burn credits, so run size is bounded."""
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "max_eval_items", 2, raising=False)
    dataset = await make_dataset_with_items(tenant, n=3)

    res = await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "dataset_too_large"


async def test_too_many_concurrent_runs_is_429(tenant, monkeypatch):
    """Spend guard 2: a mistyped loop hits a wall at the API, not the wallet."""
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "max_concurrent_runs", 1, raising=False)
    dataset = await make_dataset_with_items(tenant)

    assert (await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))).status_code == 201
    second = await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "too_many_runs"


async def test_cancel_moves_a_queued_run_to_cancelled(tenant):
    dataset = await make_dataset_with_items(tenant)
    run = (await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))).json()

    res = await tenant.client.post(f"/v1/eval-runs/{run['id']}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    # Cancelling a terminal run is a conflict, not a silent no-op.
    assert (await tenant.client.post(f"/v1/eval-runs/{run['id']}/cancel")).status_code == 409


async def test_run_list_and_results_are_tenant_scoped(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset_with_items(mine)
    run = (await mine.client.post("/v1/eval-runs", json=run_body(dataset["id"]))).json()

    listed = await mine.client.get(f"/v1/projects/{mine.project_id}/eval-runs")
    assert [r["id"] for r in listed.json()] == [run["id"]]

    assert (await theirs.client.get(f"/v1/eval-runs/{run['id']}")).status_code == 404
    assert (await theirs.client.get(f"/v1/eval-runs/{run['id']}/results")).status_code == 404
    assert (await theirs.client.post(f"/v1/eval-runs/{run['id']}/cancel")).status_code == 404
    assert (await theirs.client.get(f"/v1/projects/{mine.project_id}/eval-runs")).status_code == 404


async def test_results_page_is_empty_before_the_worker_runs(tenant):
    dataset = await make_dataset_with_items(tenant)
    run = (await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))).json()

    body = (await tenant.client.get(f"/v1/eval-runs/{run['id']}/results")).json()
    assert body == {"items": [], "total": 0}


async def test_deleting_a_dataset_with_runs_is_refused(tenant):
    dataset = await make_dataset_with_items(tenant)
    await tenant.client.post("/v1/eval-runs", json=run_body(dataset["id"]))

    res = await tenant.client.delete(f"/v1/datasets/{dataset['id']}")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "dataset_in_use"


# ---- grader catalog ---------------------------------------------------------


async def test_graders_endpoint_drives_the_ui_picker(tenant):
    res = await tenant.client.get("/v1/graders")
    assert res.status_code == 200
    graders = res.json()
    names = {g["name"] for g in graders}
    assert {"valid_json", "exact_match", "accuracy", "hallucination"} <= names
    for grader in graders:
        assert grader["kind"] in ("deterministic", "judge")
        assert grader["config_schema"]["type"] == "object"


async def test_graders_endpoint_requires_a_session(client):
    assert (await client.get("/v1/graders")).status_code == 401


# ---- grader config validation ----------------------------------------------


async def test_invalid_grader_config_is_refused_at_creation(tenant):
    """`contains` with no `value` searches for the empty string, which every
    output contains — the run would complete with a confident 1.0 having
    measured nothing. A silent pass is worse than a failure, so it 400s."""
    dataset = await make_dataset_with_items(tenant)

    res = await tenant.client.post(
        "/v1/eval-runs",
        json=run_body(dataset["id"], graders=[{"name": "contains", "config": {}}]),
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "invalid_grader_config"


async def test_a_misspelled_config_key_is_refused(tenant):
    """The catalog sets additionalProperties:false, so `text` instead of
    `value` is caught rather than silently ignored."""
    dataset = await make_dataset_with_items(tenant)

    res = await tenant.client.post(
        "/v1/eval-runs",
        json=run_body(
            dataset["id"], graders=[{"name": "contains", "config": {"text": "sorry"}}]
        ),
    )
    assert res.status_code == 400
    assert "contains" in res.json()["detail"]["message"]


async def test_a_valid_grader_config_is_accepted(tenant):
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post(
        "/v1/eval-runs",
        json=run_body(
            dataset["id"], graders=[{"name": "contains", "config": {"value": "sorry"}}]
        ),
    )
    assert res.status_code == 201, res.text


async def test_an_unknown_grader_is_not_config_validated(tenant):
    """It may be a plugin installed only in the worker image, whose schema the
    API cannot see. Accepted with a warning; a grader the worker can't resolve
    fails at the item level, which is already a supported outcome."""
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post(
        "/v1/eval-runs",
        json=run_body(
            dataset["id"],
            graders=[{"name": "house_style", "config": {"anything": True}}],
        ),
    )
    assert res.status_code == 201, res.text
