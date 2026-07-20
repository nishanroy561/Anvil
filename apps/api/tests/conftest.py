"""Test fixtures.

Smoke tests run anywhere. Integration tests need Postgres + Redis
(`docker compose up -d postgres redis`) and are skipped with a pointer when
those aren't reachable — see STATUS.md.

Everything runs against a dedicated `anvil_test` database and Redis DB 15,
both created/reset here, so a test run never touches dev data. The env vars
are set *before* `anvil_api` is imported: the app builds its engine and Redis
pool at import time from `get_settings()`.
"""

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

API_DIR = Path(__file__).resolve().parents[1]

TEST_DB = "anvil_test"
ADMIN_URL = os.environ.get(
    "ANVIL_TEST_ADMIN_URL", "postgresql+asyncpg://anvil:anvil@localhost:5432/postgres"
)
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
TEST_REDIS_URL = os.environ.get("ANVIL_TEST_REDIS_URL", "redis://localhost:6379/15")

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["SECRET_KEY"] = "test-secret"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from anvil_api.db import engine  # noqa: E402
from anvil_api.main import app  # noqa: E402
from anvil_api.models import Base  # noqa: E402
from anvil_api.redis_client import get_redis  # noqa: E402


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname, parts.port or default_port), 1.0):
            return True
    except OSError:
        return False


SERVICES_UP = _reachable(ADMIN_URL, 5432) and _reachable(TEST_REDIS_URL, 6379)

requires_services = pytest.mark.skipif(
    not SERVICES_UP,
    reason="needs Postgres+Redis: docker compose up -d postgres redis",
)


async def _recreate_database() -> None:
    import asyncpg

    parts = urlsplit(ADMIN_URL.replace("postgresql+asyncpg", "postgresql"))
    conn = await asyncpg.connect(
        host=parts.hostname,
        port=parts.port or 5432,
        user=parts.username,
        password=parts.password,
        database=(parts.path.lstrip("/") or "postgres"),
    )
    try:
        # Terminate stragglers so DROP doesn't block on a leftover connection.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            TEST_DB,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def database():
    """Fresh `anvil_test` migrated by Alembic — this also exercises the
    'clean migrate on a fresh DB' half of the Phase-1 acceptance criteria."""
    if not SERVICES_UP:
        yield
        return

    asyncio.run(_recreate_database())
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    yield


@pytest.fixture(scope="session", autouse=True)
async def close_pools(database):
    """Return connections before the session loop closes under them."""
    yield
    if SERVICES_UP:
        await engine.dispose()
        await get_redis().aclose()


@pytest.fixture(autouse=True)
async def clean_state(database):
    """Each test starts with empty tables and an empty rate-limit namespace."""
    if not SERVICES_UP:
        yield
        return

    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    await get_redis().flushdb()
    yield


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def tenant_factory():
    """Creates an isolated tenant: org + user + project + API key.

    Each tenant gets its own cookie jar and its own source IP — otherwise the
    per-IP auth limit (10/min) would start rejecting signups partway through a
    test that needs several tenants.
    """
    clients = []
    counter = 0

    async def _make(name: str = "Acme"):
        nonlocal counter
        counter += 1
        http = AsyncClient(
            transport=ASGITransport(app=app, client=(f"10.10.0.{counter}", 12345)),
            base_url="http://test",
        )
        clients.append(http)
        signup = await http.post(
            "/v1/auth/signup",
            json={
                # `.test` is a special-use TLD and the email validator rejects it.
                "email": f"tenant{counter}@example.com",
                "password": "hunter2hunter2",
                "org_name": name,
            },
        )
        assert signup.status_code == 201, signup.text
        project = (await http.get("/v1/projects")).json()[0]
        key = (
            await http.post(f"/v1/projects/{project['id']}/api-keys", json={"name": "ingest"})
        ).json()
        return SimpleNamespace(
            client=http,
            project_id=project["id"],
            key=key["key"],
            key_id=key["id"],
            headers={"Authorization": f"Bearer {key['key']}"},
        )

    yield _make

    for http in clients:
        await http.aclose()


@pytest.fixture
async def tenant(tenant_factory):
    return await tenant_factory()


@pytest.fixture
def client_for_ip():
    """Client factory that fakes the peer address, so per-IP limits are testable."""

    def _make(ip: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app, client=(ip, 12345)),
            base_url="http://test",
        )

    return _make
