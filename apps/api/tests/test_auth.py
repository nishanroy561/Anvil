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


# ---- deployment-shaped concerns (Phase 11 review) ---------------------------


async def test_session_cookie_is_secure_behind_https(client, monkeypatch):
    """A TLS deployment must not hand out a cookie a downgrade can read.

    Derived from `web_origin` rather than defaulting either way: hardcoded off
    ships a downgradeable cookie to every HTTPS install, hardcoded on makes
    localhost logins silently not persist.
    """
    from anvil_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "web_origin", "https://anvil.example.com", raising=False)
    monkeypatch.setattr(settings, "session_cookie_secure", None, raising=False)

    await client.post(
        "/v1/auth/signup",
        json={"email": "tls@example.com", "password": "hunter2hunter2", "org_name": "TLS"},
    )
    res = await client.post(
        "/v1/auth/login", json={"email": "tls@example.com", "password": "hunter2hunter2"}
    )
    assert res.status_code == 200
    assert "secure" in res.headers["set-cookie"].lower()


async def test_session_cookie_is_not_secure_on_plain_http(client, monkeypatch):
    from anvil_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "web_origin", "http://localhost:3000", raising=False)
    monkeypatch.setattr(settings, "session_cookie_secure", None, raising=False)

    await client.post(
        "/v1/auth/signup",
        json={"email": "plain@example.com", "password": "hunter2hunter2", "org_name": "Plain"},
    )
    res = await client.post(
        "/v1/auth/login", json={"email": "plain@example.com", "password": "hunter2hunter2"}
    )
    assert res.status_code == 200
    assert "secure" not in res.headers["set-cookie"].lower()


async def test_an_explicit_cookie_secure_setting_wins(client, monkeypatch):
    from anvil_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "web_origin", "http://localhost:3000", raising=False)
    monkeypatch.setattr(settings, "session_cookie_secure", True, raising=False)

    await client.post(
        "/v1/auth/signup",
        json={"email": "forced@example.com", "password": "hunter2hunter2", "org_name": "Forced"},
    )
    res = await client.post(
        "/v1/auth/login", json={"email": "forced@example.com", "password": "hunter2hunter2"}
    )
    assert "secure" in res.headers["set-cookie"].lower()


async def test_an_unapplied_forwarded_header_skips_the_per_ip_limit(client_for_ip, caplog):
    """Behind a misconfigured proxy the per-IP bucket would be shared by everyone,
    so the eleventh login of the minute fails for an unrelated user. Better to warn
    and lean on the per-email limit than to throttle the whole install.

    The header is never trusted directly — doing so would let a caller choose its
    own bucket and bypass the limit outright.
    """
    import logging

    http = client_for_ip("10.20.0.1")
    try:
        with caplog.at_level(logging.WARNING):
            codes = [
                (
                    await http.post(
                        "/v1/auth/login",
                        json={"email": f"nobody{i}@example.com", "password": "wrong-password"},
                        # A proxy uvicorn was not told to trust.
                        headers={"X-Forwarded-For": "203.0.113.9"},
                    )
                ).status_code
                for i in range(14)
            ]
        # Each attempt uses a different email, so the per-email limit never trips
        # and the per-IP limit is the only thing that could have 429'd.
        assert 429 not in codes, "the shared-bucket per-IP limit throttled unrelated users"
        assert "FORWARDED_ALLOW_IPS" in caplog.text
    finally:
        await http.aclose()


async def test_the_per_ip_limit_still_applies_with_no_proxy(client_for_ip):
    """The direct-connection case is unchanged: 10/min per address."""
    http = client_for_ip("10.20.0.2")
    try:
        codes = [
            (
                await http.post(
                    "/v1/auth/login",
                    json={"email": f"direct{i}@example.com", "password": "wrong-password"},
                )
            ).status_code
            for i in range(13)
        ]
        assert 429 in codes
    finally:
        await http.aclose()
