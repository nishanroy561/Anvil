"""Worker test fixtures.

Same shape as `apps/api/tests/conftest.py`: a dedicated `anvil_test` database
and Redis DB 15, skipped with a pointer when the services aren't up. Migrations
are driven from `apps/api` — the API owns the schema (ADR-0014), so the worker
suite migrates by invoking its Alembic rather than declaring anything itself.
"""

import asyncio
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

WORKER_DIR = Path(__file__).resolve().parents[1]
API_DIR = WORKER_DIR.parent / "api"

TEST_DB = "anvil_test"
ADMIN_URL = os.environ.get(
    "ANVIL_TEST_ADMIN_URL", "postgresql+asyncpg://anvil:anvil@localhost:5432/postgres"
)
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
TEST_REDIS_URL = os.environ.get("ANVIL_TEST_REDIS_URL", "redis://localhost:6379/15")

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL

import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import text  # noqa: E402

from anvil_worker.db import engine  # noqa: E402
from anvil_worker.ingest_consumer import IngestConsumer  # noqa: E402


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

async def truncate_everything(conn) -> None:
    """Empty every table except Alembic's own bookkeeping.

    Read from the catalog rather than hardcoded. The hardcoded list named ten of
    the sixteen tables and worked anyway, because the six it missed —
    `api_keys`, `monitor_configs`, `monitor_scores`, `alert_rules`,
    `alert_events`, `org_usage` — all have an FK path back to one that *was*
    listed, so `CASCADE` reached them. That is a real dependency on something no
    reader of the list can see, and it holds only until someone adds a table with
    no such path, at which point state leaks between tests and the failure looks
    like a flaky test rather than a fixture bug.
    """
    names = (
        await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
            )
        )
    ).scalars().all()
    if names:
        joined = ", ".join(f'"{n}"' for n in names)
        await conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))


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
async def close_engine(database):
    yield
    if SERVICES_UP:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _keyless(monkeypatch):
    """The suite never touches a real provider (CLAUDE.md: mock all LLM calls).

    Two guards, because either alone leaks: clearing the keys is undone by
    `load_env()` reading the repo-root `.env` (which a developer's box has, with
    live keys in it), and blocking the load alone leaves keys that were exported
    into the shell. A test that wants a provider injects a fake through
    `model_factory`.
    """
    from anvil_worker import dogfood, providers

    monkeypatch.setattr(providers, "_env_loaded", True)
    for env_var, _prefixes, _default in providers.PROVIDERS:
        monkeypatch.delenv(env_var, raising=False)

    # Judge tracing is a network client, not a provider, but it leaks the same
    # way: a developer's `.env` carries a real internal key, and the suite would
    # ship test judge traces into a live project. Off, and memoisation cleared
    # so an enabling test can't leave a client behind for the next one.
    monkeypatch.delenv(dogfood.API_KEY_ENV, raising=False)
    monkeypatch.setattr(dogfood, "_client", None)
    monkeypatch.setattr(dogfood, "_resolved", False)


@pytest.fixture
async def redis_client():
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def clean_state(database):
    if not SERVICES_UP:
        yield
        return

    async with engine.begin() as conn:
        await truncate_everything(conn)
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    yield


@pytest.fixture
async def project_factory():
    """Traces carry an FK to projects, so tests need real tenants.

    Returns project_id. Each call creates its own org, which is what makes
    cross-tenant tests meaningful.
    """

    async def _make(name: str = "Test Project") -> uuid.UUID:
        org_id, project_id = uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, plan) "
                    "VALUES (:id, :name, 'enterprise')"
                ),
                {"id": org_id, "name": f"{name} Org"},
            )
            await conn.execute(
                text(
                    "INSERT INTO projects (id, org_id, name, slug) "
                    "VALUES (:id, :org_id, :name, :slug)"
                ),
                {"id": project_id, "org_id": org_id, "name": name, "slug": name.lower()},
            )
        return project_id

    return _make


@pytest.fixture
async def consumer(redis_client):
    """A consumer with a fixed name, so tests can reason about pending entries."""
    instance = IngestConsumer(redis_client, consumer_name="test-consumer")
    await instance.ensure_group()
    return instance
