"""Phase-1 acceptance: tenant isolation (404, never 403) and API-key lifecycle.

Covers edge-case checklist row 17's sibling concern — a credential only ever
resolves resources inside its own org, and a revoked key resolves nothing.
"""

from fastapi import Depends

from anvil_api.auth import current_project_from_api_key
from anvil_api.main import app
from anvil_api.models import Project

from .conftest import requires_services

pytestmark = requires_services


# No API-key-authenticated route exists until Phase 2's ingest endpoint, so we
# mount one here to exercise `current_project_from_api_key` over real HTTP.
@app.get("/_test/whoami", include_in_schema=False)
async def _whoami(project: Project = Depends(current_project_from_api_key)):
    return {"project_id": str(project.id)}


async def _org(client, email: str, org_name: str) -> dict:
    """Signs up an org and returns its default project + a fresh API key."""
    await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "hunter2hunter2", "org_name": org_name},
    )
    project = (await client.get("/v1/projects")).json()[0]
    key = await client.post(
        f"/v1/projects/{project['id']}/api-keys", json={"name": "ingest"}
    )
    return {"project": project, "key": key.json()}


async def test_api_key_is_returned_once_and_only_by_prefix_afterwards(client):
    acme = await _org(client, "a@example.com", "Acme")
    project_id, created = acme["project"]["id"], acme["key"]

    assert created["key"].startswith("ak_")
    assert created["key_prefix"] == "ak_" + created["key"].split("_")[1]

    listed = (await client.get(f"/v1/projects/{project_id}/api-keys")).json()
    assert len(listed) == 1
    assert listed[0]["key_prefix"] == created["key_prefix"]
    assert "key" not in listed[0]  # the secret never comes back


async def test_valid_key_resolves_its_project_and_revoked_key_is_rejected(client):
    acme = await _org(client, "a@example.com", "Acme")
    full_key, key_id = acme["key"]["key"], acme["key"]["id"]
    auth = {"Authorization": f"Bearer {full_key}"}

    res = await client.get("/_test/whoami", headers=auth)
    assert res.status_code == 200
    assert res.json()["project_id"] == acme["project"]["id"]

    assert (await client.delete(f"/v1/api-keys/{key_id}")).status_code == 204

    revoked = await client.get("/_test/whoami", headers=auth)
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "invalid_api_key"


async def test_missing_and_malformed_api_keys_are_rejected(client):
    for headers in [{}, {"Authorization": "Bearer nonsense"}, {"Authorization": "ak_abc"}]:
        res = await client.get("/_test/whoami", headers=headers)
        assert res.status_code == 401, headers


async def test_other_tenants_resources_404_never_403(client, client_for_ip):
    acme = await _org(client, "a@example.com", "Acme")
    acme_project_id, acme_key_id = acme["project"]["id"], acme["key"]["id"]

    # Second org, separate cookie jar. A separate IP keeps the two signups from
    # sharing the per-IP auth limit.
    async with client_for_ip("10.0.0.2") as other:
        globex = await _org(other, "b@example.com", "Globex")
        assert globex["project"]["id"] != acme_project_id

        # Globex sees only its own projects.
        assert [p["id"] for p in (await other.get("/v1/projects")).json()] == [
            globex["project"]["id"]
        ]

        for res in [
            await other.get(f"/v1/projects/{acme_project_id}/api-keys"),
            await other.post(
                f"/v1/projects/{acme_project_id}/api-keys", json={"name": "steal"}
            ),
            await other.delete(f"/v1/api-keys/{acme_key_id}"),
        ]:
            assert res.status_code == 404, res.request.url
            assert res.json()["detail"]["code"] == "not_found"

    # Acme's key still works — nothing was revoked by the cross-tenant attempt.
    whoami = await client.get(
        "/_test/whoami", headers={"Authorization": f"Bearer {acme['key']['key']}"}
    )
    assert whoami.status_code == 200


async def test_unknown_project_id_also_404s(client):
    await _org(client, "a@example.com", "Acme")
    res = await client.get("/v1/projects/00000000-0000-0000-0000-000000000000/api-keys")
    assert res.status_code == 404
