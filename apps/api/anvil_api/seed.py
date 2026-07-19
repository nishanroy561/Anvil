"""Seed the demo org/user/project/API key. Run: python -m anvil_api.seed

Demo org is on plan `enterprise` so demos and load tests never trip
cloud-mode quotas (plan/architecture.md §7)."""

import asyncio

from sqlalchemy import select

from .auth import generate_api_key, hash_password
from .db import SessionLocal
from .models import ApiKey, Organization, Project, User

DEMO_EMAIL = "demo@anvil.dev"
DEMO_PASSWORD = "demo1234"


async def seed() -> None:
    async with SessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        if existing.scalar_one_or_none() is not None:
            print(f"Seed already present ({DEMO_EMAIL}); nothing to do.")
            return

        org = Organization(name="Demo Org", plan="enterprise")
        session.add(org)
        await session.flush()
        session.add(
            User(org_id=org.id, email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
        )
        project = Project(org_id=org.id, name="Demo Project", slug="demo-project")
        session.add(project)
        await session.flush()
        full, prefix, key_hash = generate_api_key()
        session.add(
            ApiKey(project_id=project.id, key_hash=key_hash, key_prefix=prefix, name="seed")
        )
        await session.commit()

        print("Seeded demo data:")
        print(f"  login:    {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"  project:  {project.id}")
        print(f"  API key (save it — shown only once): {full}")


if __name__ == "__main__":
    asyncio.run(seed())
