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


def test_a_blank_retention_days_reads_as_unset(monkeypatch):
    """`RETENTION_DAYS=` is the documented default, and it must not be a parse
    error. Found by `docker compose up` on a clean volume: compose renders
    `${RETENTION_DAYS:-}` as an empty string, and the API crash-looped at import
    on its own default configuration.
    """
    from anvil_api.config import Settings

    monkeypatch.setenv("RETENTION_DAYS", "")
    assert Settings(_env_file=None).retention_days is None

    monkeypatch.setenv("RETENTION_DAYS", "   ")
    assert Settings(_env_file=None).retention_days is None

    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert Settings(_env_file=None).retention_days == 30
