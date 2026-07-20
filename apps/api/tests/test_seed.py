"""Phase-1 acceptance: the seeder works and is safe to re-run.

The demo org/user/project/key is the harness for every later phase's manual
flows, so a broken seeder blocks the whole build.
"""

from sqlalchemy import select

from anvil_api.db import SessionLocal
from anvil_api.models import ApiKey, Organization, Project, User
from anvil_api.seed import DEMO_EMAIL, DEMO_PASSWORD, seed

from .conftest import requires_services

pytestmark = requires_services


async def test_seed_creates_demo_tenant_on_enterprise_plan(client, capsys):
    await seed()

    async with SessionLocal() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        user = (await session.execute(select(User))).scalar_one()
        project = (await session.execute(select(Project))).scalar_one()
        key = (await session.execute(select(ApiKey))).scalar_one()

    # Enterprise so seeded data and the Phase-11 50k load test never trip the
    # Free-tier quota (plan/architecture.md §7 step 5).
    assert org.plan == "enterprise"
    assert user.email == DEMO_EMAIL
    assert project.org_id == org.id
    assert key.project_id == project.id
    assert key.revoked_at is None

    # The full key is printed exactly once and never stored in the clear.
    printed = capsys.readouterr().out
    assert "ak_" in printed
    assert key.key_hash not in printed

    # Seeded credentials actually log in.
    res = await client.post(
        "/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    )
    assert res.status_code == 200


async def test_seed_is_idempotent(capsys):
    await seed()
    capsys.readouterr()

    await seed()
    assert "already present" in capsys.readouterr().out

    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1
