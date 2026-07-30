"""Eval APIs reached with an API key instead of a session (ADR-0021).

This is what makes CI gating possible: a GitHub Action holds a project-scoped
key in a secret, not a user's email and password. The tenancy rules have to
hold identically on the new path — an API key reaching another project must
404 exactly like a session reaching another org.
"""

import uuid

from httpx import ASGITransport, AsyncClient

from anvil_api.main import app

from .conftest import requires_services

pytestmark = requires_services


def keyed_client(key: str, ip: str = "10.20.0.1") -> AsyncClient:
    """A client with the API key and **no session cookie** — what CI looks
    like. Reusing the tenant fixture's client would leave a cookie in the jar
    and prove nothing about key-only access."""
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 12345)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


async def make_dataset_with_items(tenant, n=3):
    dataset = (
        await tenant.client.post(
            f"/v1/projects/{tenant.project_id}/datasets", json={"name": "CI golden"}
        )
    ).json()
    await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/items",
        json={"items": [{"input": {"q": f"q{i}"}, "expected_output": "a"} for i in range(n)]},
    )
    return dataset


RUN_BODY = {
    "name": "ci",
    "target": {"mode": "rescore"},
    "graders": [{"name": "exact_match", "config": {}}],
}


async def test_an_api_key_can_create_and_read_an_eval_run(tenant):
    dataset = await make_dataset_with_items(tenant)
    async with keyed_client(tenant.key) as ci:
        created = await ci.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]

        assert (await ci.get(f"/v1/eval-runs/{run_id}")).status_code == 200
        assert (await ci.get(f"/v1/eval-runs/{run_id}/results")).status_code == 200
        listed = await ci.get(f"/v1/projects/{tenant.project_id}/eval-runs")
        assert listed.status_code == 200
        assert [r["id"] for r in listed.json()] == [run_id]


async def test_an_api_key_can_reach_the_compare_endpoint(tenant):
    """The regression gate is the whole reason CI calls this API."""
    dataset = await make_dataset_with_items(tenant)
    async with keyed_client(tenant.key) as ci:
        a = (await ci.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})).json()
        b = (await ci.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})).json()
        res = await ci.get(f"/v1/eval-runs/compare?a={a['id']}&b={b['id']}")
        assert res.status_code == 200, res.text


async def test_an_api_key_can_read_datasets(tenant):
    dataset = await make_dataset_with_items(tenant)
    async with keyed_client(tenant.key) as ci:
        assert (await ci.get(f"/v1/datasets/{dataset['id']}")).status_code == 200
        assert (await ci.get(f"/v1/datasets/{dataset['id']}/items")).status_code == 200


async def test_a_key_cannot_reach_another_tenants_run(tenant_factory):
    """404, never 403 — the rule holds on the key path too."""
    mine, theirs = await tenant_factory(), await tenant_factory()
    dataset = await make_dataset_with_items(mine)
    run = (
        await mine.client.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})
    ).json()

    async with keyed_client(theirs.key, ip="10.20.0.2") as intruder:
        assert (await intruder.get(f"/v1/eval-runs/{run['id']}")).status_code == 404
        assert (await intruder.get(f"/v1/eval-runs/{run['id']}/results")).status_code == 404
        assert (await intruder.get(f"/v1/datasets/{dataset['id']}")).status_code == 404
        # Creating a run against someone else's dataset is the dangerous one:
        # it would spend the intruder's grader budget on the victim's data.
        created = await intruder.post(
            "/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]}
        )
        assert created.status_code == 404


async def test_a_key_cannot_list_another_projects_runs(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    async with keyed_client(theirs.key, ip="10.20.0.3") as intruder:
        res = await intruder.get(f"/v1/projects/{mine.project_id}/eval-runs")
        assert res.status_code == 404


async def test_a_revoked_key_is_refused(tenant):
    dataset = await make_dataset_with_items(tenant)
    await tenant.client.delete(f"/v1/api-keys/{tenant.key_id}")

    async with keyed_client(tenant.key) as ci:
        res = await ci.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "invalid_api_key"


async def test_no_credential_at_all_is_a_401(tenant):
    dataset = await make_dataset_with_items(tenant)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("10.20.0.4", 1)), base_url="http://test"
    ) as anon:
        res = await anon.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})
        assert res.status_code == 401


async def test_a_garbage_key_is_refused_rather_than_falling_back_to_the_cookie(tenant):
    """A caller that presents a key means it. Falling through to a session
    cookie that happens to be in the jar would let a CI job silently run as
    whoever last logged in on that machine."""
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post(
        "/v1/eval-runs",
        json={**RUN_BODY, "dataset_id": dataset["id"]},
        headers={"Authorization": "Bearer ak_deadbeef_nope"},
    )
    assert res.status_code == 401, "a bad key was silently downgraded to the session cookie"


async def test_the_session_path_still_works(tenant):
    """The dashboard must be unaffected by the key path being added."""
    dataset = await make_dataset_with_items(tenant)
    res = await tenant.client.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": dataset["id"]})
    assert res.status_code == 201


async def test_an_unknown_dataset_is_a_404_for_a_key_caller(tenant):
    async with keyed_client(tenant.key) as ci:
        res = await ci.post("/v1/eval-runs", json={**RUN_BODY, "dataset_id": str(uuid.uuid4())})
        assert res.status_code == 404
