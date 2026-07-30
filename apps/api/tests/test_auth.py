"""Phase-1 acceptance: signup/login/session flow (plan/architecture.md §7 step 6)."""

import pytest

from anvil_api.auth import SESSION_COOKIE

from .conftest import requires_services

pytestmark = requires_services


async def test_signup_creates_org_user_and_default_project(client):
    res = await client.post(
        "/v1/auth/signup",
        json={"email": "Founder@Example.com", "password": "hunter2hunter2", "org_name": "Acme"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "founder@example.com"  # stored lowercased
    assert client.cookies.get(SESSION_COOKIE)

    # The session cookie alone authenticates, and signup seeded one project.
    projects = await client.get("/v1/projects")
    assert projects.status_code == 200
    assert [p["slug"] for p in projects.json()] == ["default"]


async def test_signup_rejects_duplicate_email_without_confirming_it_exists(client):
    payload = {"email": "dup@example.com", "password": "hunter2hunter2", "org_name": "Acme"}
    assert (await client.post("/v1/auth/signup", json=payload)).status_code == 201

    res = await client.post("/v1/auth/signup", json=payload)
    assert res.status_code == 400
    # No user enumeration: the message must not distinguish "taken" from any
    # other validation failure.
    assert res.json()["error"]["code"] == "signup_failed"
    assert "dup@example.com" not in res.text


async def test_login_succeeds_and_wrong_password_is_generic_401(client):
    await client.post(
        "/v1/auth/signup",
        json={"email": "user@example.com", "password": "hunter2hunter2", "org_name": "Acme"},
    )
    client.cookies.clear()

    ok = await client.post(
        "/v1/auth/login", json={"email": "user@example.com", "password": "hunter2hunter2"}
    )
    assert ok.status_code == 200
    assert (await client.get("/v1/auth/me")).json()["email"] == "user@example.com"

    bad = await client.post(
        "/v1/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )
    missing = await client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert bad.status_code == missing.status_code == 401
    # Identical body for "wrong password" and "no such user".
    assert bad.json() == missing.json()


async def test_unauthenticated_and_tampered_sessions_are_rejected(client):
    assert (await client.get("/v1/projects")).status_code == 401

    client.cookies.set(SESSION_COOKIE, "not-a-signed-token")
    res = await client.get("/v1/projects")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthenticated"


async def test_logout_clears_the_session(client):
    await client.post(
        "/v1/auth/signup",
        json={"email": "bye@example.com", "password": "hunter2hunter2", "org_name": "Acme"},
    )
    assert (await client.get("/v1/projects")).status_code == 200

    assert (await client.post("/v1/auth/logout")).status_code == 204
    assert (await client.get("/v1/projects")).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "hunter2hunter2", "org_name": "Acme"},
        {"email": "short@example.com", "password": "short", "org_name": "Acme"},
        {"email": "noorg@example.com", "password": "hunter2hunter2", "org_name": ""},
    ],
)
async def test_signup_validation(client, payload):
    assert (await client.post("/v1/auth/signup", json=payload)).status_code == 422
