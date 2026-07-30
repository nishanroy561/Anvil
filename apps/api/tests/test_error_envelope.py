"""The single error shape and the request id (Phase 11 §2).

Every other suite asserts `["error"]["code"]` on its own failures; this one pins
the envelope itself, including the three cases no router hand-formats — FastAPI's
validation error, a status raised by the framework, and an unhandled exception.
"""

import uuid


from anvil_api.errors import REQUEST_ID_HEADER
from anvil_api.main import app

from .conftest import requires_services

pytestmark = requires_services


def assert_envelope(body: dict) -> dict:
    """The contract: exactly one top-level key, `code` and `message` inside it."""
    assert set(body) == {"error"}, body
    error = body["error"]
    assert isinstance(error.get("code"), str) and error["code"]
    assert isinstance(error.get("message"), str)
    return error


# ---- the shape ---------------------------------------------------------------


async def test_a_router_error_uses_the_envelope(client):
    res = await client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert res.status_code == 401
    assert assert_envelope(res.json())["code"] == "invalid_credentials"


async def test_an_unauthenticated_read_uses_the_envelope(client):
    res = await client.get("/v1/projects")
    assert res.status_code == 401
    assert assert_envelope(res.json())["code"] == "unauthenticated"


async def test_a_framework_404_gets_a_code(client):
    """Nothing raises this — Starlette does, for a path with no route. A bare
    `{"detail": "Not Found"}` gives a client nothing to branch on."""
    res = await client.get("/v1/no-such-endpoint")
    assert res.status_code == 404
    assert assert_envelope(res.json())["code"] == "not_found"


async def test_a_405_gets_a_code(client):
    res = await client.delete("/v1/auth/login")
    assert res.status_code == 405
    assert assert_envelope(res.json())["code"] == "method_not_allowed"


async def test_validation_errors_keep_their_per_field_detail(client):
    """FastAPI's default 422 is a *list* — the one shape that can't share the
    envelope. It moves under `error.fields` rather than being flattened away."""
    res = await client.post("/v1/auth/signup", json={"email": "not-an-email"})
    assert res.status_code == 422

    error = assert_envelope(res.json())
    assert error["code"] == "invalid_request"
    assert error["field_count"] >= 1
    assert len(error["fields"]) >= 1
    first = error["fields"][0]
    assert set(first) == {"path", "message", "type"}
    # The path is what tells an SDK author which field to fix.
    assert any("email" in f["path"] or "password" in f["path"] for f in error["fields"])


async def test_a_routers_extra_keys_survive_the_envelope(client, tenant):
    """Ingest attaches per-trace `errors`; the envelope carries a router's own
    diagnostic keys through instead of reducing everything to code+message."""
    res = await client.post(
        "/v1/ingest/traces",
        json={"traces": [{"id": "not-a-uuid", "name": "x", "started_at": "nope"}]},
        headers=tenant.headers,
    )
    assert res.status_code == 422
    error = assert_envelope(res.json())
    assert error["code"] == "invalid_payload"
    assert error["errors"] and "path" in error["errors"][0]
    assert error["error_count"] >= 1


async def test_headers_survive_the_envelope(client, tenant, monkeypatch):
    """A 429 without Retry-After is a 429 a client cannot back off from — the
    reshaping must not drop the headers the status is useful because of."""
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "feedback_rate_limit_pm", 1, raising=False)
    body = {"trace_id": str(uuid.uuid4()), "rating": -1}
    await client.post("/v1/feedback", json=body, headers=tenant.headers)
    limited = await client.post("/v1/feedback", json=body, headers=tenant.headers)

    assert limited.status_code == 429
    assert assert_envelope(limited.json())["code"] == "rate_limited"
    assert limited.headers["Retry-After"]


# ---- unhandled errors --------------------------------------------------------


async def test_an_unhandled_error_becomes_a_clean_500():
    """No traceback on the wire, and a code the client can branch on.

    Needs its own transport: `ASGITransport` re-raises app exceptions by default,
    which is right for most tests and hides the very handler under test here.
    """
    from httpx import ASGITransport, AsyncClient

    @app.get("/v1/_boom_test")
    async def boom():  # pragma: no cover - the raise is the point
        raise RuntimeError("a secret internal detail")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw:
            res = await raw.get("/v1/_boom_test")

        assert res.status_code == 500
        error = assert_envelope(res.json())
        assert error["code"] == "internal_error"
        assert "secret internal detail" not in res.text
        assert "Traceback" not in res.text
        # It points at the one thing that lets an operator find the log line.
        assert REQUEST_ID_HEADER.lower() in error["message"].lower()
        # And that thing has to actually be on the response. It was not: an
        # unhandled exception is raised straight through the request-id
        # middleware, so the middleware never ran its tail and the header was
        # missing on exactly the responses whose body says to quote it.
        assert res.headers.get(REQUEST_ID_HEADER), (
            "the 500 body tells the caller to quote a header that isn't there"
        )
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/v1/_boom_test"
        ]


# ---- request id --------------------------------------------------------------


async def test_every_response_carries_a_request_id(client, tenant):
    """Success, a router error, a framework error, and a validation error — the id
    is set in the handlers as well as the middleware, so no response shape depends
    on middleware ordering to get one."""
    ok = await tenant.client.get("/v1/projects")
    unauth = await client.get("/v1/projects")
    missing = await client.get("/v1/no-such-endpoint")
    invalid = await client.post("/v1/auth/signup", json={"email": "nope"})

    assert (ok.status_code, unauth.status_code, missing.status_code, invalid.status_code) == (
        200, 401, 404, 422,
    )
    for res in (ok, unauth, missing, invalid):
        assert res.headers[REQUEST_ID_HEADER], res.status_code


async def test_request_ids_differ_between_requests(client):
    a = await client.get("/v1/projects")
    b = await client.get("/v1/projects")
    assert a.headers[REQUEST_ID_HEADER] != b.headers[REQUEST_ID_HEADER]


async def test_an_inbound_request_id_is_honoured(client):
    """One identifier across a proxy hop beats two unrelated ones."""
    res = await client.get("/v1/projects", headers={REQUEST_ID_HEADER: "trace-me-123"})
    assert res.headers[REQUEST_ID_HEADER] == "trace-me-123"


async def test_an_absurd_inbound_request_id_is_truncated(client):
    """This value reaches log lines, so its length is bounded."""
    res = await client.get("/v1/projects", headers={REQUEST_ID_HEADER: "x" * 500})
    assert len(res.headers[REQUEST_ID_HEADER]) <= 64


async def test_the_request_id_is_not_in_the_body(client):
    """Deliberate: two identical failures must produce byte-identical bodies, or
    the generic-401 anti-enumeration property stops being auditable."""
    a = await client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"}
    )
    b = await client.post(
        "/v1/auth/login", json={"email": "b@example.com", "password": "wrong-password"}
    )
    assert a.json() == b.json()
    assert "request_id" not in a.text


# ---- the session read backstop ----------------------------------------------


async def test_dashboard_reads_have_a_backstop(tenant, monkeypatch):
    """Every other surface had a limiter; the dozens of session read routes had
    none, so a page stuck in a refresh loop had nothing stopping it."""
    from anvil_api.config import get_settings
    from anvil_api.redis_client import get_redis

    monkeypatch.setattr(get_settings(), "session_read_limit_pm", 3, raising=False)
    # The `tenant` fixture already spent a read building the tenant, so the
    # window starts from a known-clean slate rather than an assumed one.
    await get_redis().flushdb()

    codes = [(await tenant.client.get("/v1/projects")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]

    limited = await tenant.client.get("/v1/projects")
    assert assert_envelope(limited.json())["code"] == "rate_limited"


async def test_the_backstop_does_not_meter_writes(tenant, monkeypatch):
    """A limiter must not become the reason a form submission fails."""
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "session_read_limit_pm", 1, raising=False)
    await tenant.client.get("/v1/projects")  # spends the single read

    created = await tenant.client.post("/v1/projects", json={"name": "still works"})
    assert created.status_code == 201


async def test_one_sessions_reads_do_not_throttle_another(tenant_factory, monkeypatch):
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "session_read_limit_pm", 2, raising=False)
    noisy = await tenant_factory("Noisy")
    quiet = await tenant_factory("Quiet")

    for _ in range(4):
        await noisy.client.get("/v1/projects")

    assert (await quiet.client.get("/v1/projects")).status_code == 200


async def test_the_backstop_can_be_switched_off(tenant, monkeypatch):
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "session_read_limit_pm", 0, raising=False)
    codes = [(await tenant.client.get("/v1/projects")).status_code for _ in range(6)]
    assert set(codes) == {200}
