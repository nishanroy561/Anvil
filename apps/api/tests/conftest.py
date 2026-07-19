"""Test fixtures. Integration tests need Postgres + Redis running
(docker compose up postgres redis). See STATUS.md for the sequence."""

import pytest
from httpx import ASGITransport, AsyncClient

from anvil_api.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
