"""Smoke tests runnable without backing services: they assert shape, not
connectivity. The Phase-1 acceptance tests (signup/login, tenant 404s, rate
limits, seed) live in the sibling modules and need Postgres+Redis."""


async def test_healthz_shape(client):
    res = await client.get("/healthz")
    body = res.json()
    assert set(body) == {"db", "redis"}
    assert res.status_code in (200, 503)


async def test_openapi_has_auth_routes(client):
    res = await client.get("/openapi.json")
    paths = res.json()["paths"]
    for route in ["/v1/auth/signup", "/v1/auth/login", "/v1/projects", "/healthz"]:
        assert route in paths
