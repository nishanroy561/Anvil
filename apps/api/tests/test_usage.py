"""Usage rollups read API (Phase 10, plan/retention.md §2).

What is *absent* is as load-bearing as what is present: no quota, no
percentage-of-limit, no plan field. Anvil has no tiers (ADR-0019), and a panel
that implies a ceiling implies an upgrade.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from anvil_api.db import engine

from .conftest import requires_services

pytestmark = requires_services


def month_of(offset: int = 0) -> date:
    """First of the month, `offset` months back."""
    today = datetime.now(timezone.utc).date().replace(day=1)
    for _ in range(offset):
        today = (today - timedelta(days=1)).replace(day=1)
    return today


async def org_id_of(project_id: str) -> uuid.UUID:
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text("SELECT org_id FROM projects WHERE id = :id"),
                {"id": uuid.UUID(project_id)},
            )
        ).scalar()


async def insert_usage(
    org_id: uuid.UUID, month: date, traces: int = 0, items: int = 0
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO org_usage (org_id, month, traces_ingested, eval_items, "
                "updated_at) VALUES (:org, :month, :t, :i, :now)"
            ),
            {
                "org": org_id,
                "month": month,
                "t": traces,
                "i": items,
                "now": datetime.now(timezone.utc),
            },
        )


async def test_usage_is_empty_before_the_worker_has_run(tenant):
    """The panel has to render on a fresh install, where no daily pass has
    happened yet."""
    body = (await tenant.client.get("/v1/usage")).json()
    assert body == {
        "months": [],
        "retention_days": None,
        "current_month": None,
    }


async def test_usage_returns_months_oldest_first(tenant):
    org = await org_id_of(tenant.project_id)
    await insert_usage(org, month_of(2), traces=100, items=10)
    await insert_usage(org, month_of(1), traces=200, items=20)
    await insert_usage(org, month_of(0), traces=50, items=5)

    body = (await tenant.client.get("/v1/usage")).json()
    assert [m["month"] for m in body["months"]] == [
        month_of(2).isoformat(),
        month_of(1).isoformat(),
        month_of(0).isoformat(),
    ]
    assert [m["traces_ingested"] for m in body["months"]] == [100, 200, 50]


async def test_current_month_is_pulled_out(tenant):
    org = await org_id_of(tenant.project_id)
    await insert_usage(org, month_of(1), traces=999)
    await insert_usage(org, month_of(0), traces=42, items=7)

    body = (await tenant.client.get("/v1/usage")).json()
    assert body["current_month"]["month"] == month_of(0).isoformat()
    assert body["current_month"]["traces_ingested"] == 42
    assert body["current_month"]["eval_items"] == 7


async def test_current_month_is_null_when_only_older_months_exist(tenant):
    org = await org_id_of(tenant.project_id)
    await insert_usage(org, month_of(3), traces=5)
    body = (await tenant.client.get("/v1/usage")).json()
    assert body["current_month"] is None
    assert len(body["months"]) == 1


async def test_the_months_window_is_bounded(tenant):
    org = await org_id_of(tenant.project_id)
    for offset in range(6):
        await insert_usage(org, month_of(offset), traces=offset)

    body = (await tenant.client.get("/v1/usage?months=3")).json()
    # The three most recent, still returned oldest first.
    assert [m["month"] for m in body["months"]] == [
        month_of(2).isoformat(),
        month_of(1).isoformat(),
        month_of(0).isoformat(),
    ]

    assert (await tenant.client.get("/v1/usage?months=0")).status_code == 422
    assert (await tenant.client.get("/v1/usage?months=999")).status_code == 422


async def test_retention_window_is_reported(tenant, monkeypatch):
    """A count means something different under a 30-day policy than under
    keep-forever, so the panel is told which."""
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "retention_days", 30, raising=False)
    assert (await tenant.client.get("/v1/usage")).json()["retention_days"] == 30

    # A window below 1 is reported as unset, matching the worker's refusal to act
    # on it rather than implying a policy that isn't enforced.
    monkeypatch.setattr(get_settings(), "retention_days", 0, raising=False)
    assert (await tenant.client.get("/v1/usage")).json()["retention_days"] is None


async def test_storage_bytes_is_null(tenant):
    """Per-org bytes are not honestly attributable (ADR-0022)."""
    org = await org_id_of(tenant.project_id)
    await insert_usage(org, month_of(0), traces=1)
    body = (await tenant.client.get("/v1/usage")).json()
    assert body["months"][0]["storage_bytes"] is None


async def test_usage_carries_no_quota_or_plan_fields(tenant):
    """ADR-0019: no tiers. A `limit` or `plan` on this response would be the seed
    of a quota bar, and there is nothing to be over."""
    org = await org_id_of(tenant.project_id)
    await insert_usage(org, month_of(0), traces=1)
    body = (await tenant.client.get("/v1/usage")).json()

    assert set(body) == {"months", "retention_days", "current_month"}
    assert set(body["months"][0]) == {
        "month", "traces_ingested", "eval_items", "storage_bytes", "updated_at",
    }


async def test_usage_is_org_scoped(tenant_factory):
    """No path parameter, so there is no route by which one org could ask for
    another's — but the session scoping still gets pinned."""
    mine = await tenant_factory("Mine")
    theirs = await tenant_factory("Theirs")

    await insert_usage(await org_id_of(theirs.project_id), month_of(0), traces=777)
    await insert_usage(await org_id_of(mine.project_id), month_of(0), traces=3)

    body = (await mine.client.get("/v1/usage")).json()
    assert [m["traces_ingested"] for m in body["months"]] == [3]


async def test_usage_requires_a_session(client, tenant):
    assert (await client.get("/v1/usage")).status_code == 401
    # An API key is not a session: usage is a dashboard read, and nothing in CI
    # or an SDK has any use for it.
    assert (await client.get("/v1/usage", headers=tenant.headers)).status_code == 401
