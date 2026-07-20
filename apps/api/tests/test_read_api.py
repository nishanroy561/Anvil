"""Read APIs consumed by the dashboard (plan/ingestion.md §5).

Traces are inserted directly here rather than pushed through the pipeline: this
suite is about query correctness, tenant scoping, and the search fences. The
pipeline's own round trip is covered in the worker suite.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from anvil_api.db import engine

from .conftest import requires_services

pytestmark = requires_services


def ago(**kwargs) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


async def insert_trace(
    project_id: str,
    *,
    name: str = "rag query",
    status: str = "ok",
    model: str | None = "claude-sonnet-5",
    started_at: datetime | None = None,
    latency_ms: int | None = 120,
    cost: float | None = 0.01,
    tokens_in: int | None = 100,
    tokens_out: int | None = 50,
    search_text: str | None = None,
    metadata: str = '{"env": "prod"}',
    spans: int = 0,
) -> str:
    trace_id = uuid.uuid4()
    started = started_at or ago(minutes=5)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, model, started_at, "
                "ended_at, latency_ms, total_cost_usd, total_tokens_in, total_tokens_out, "
                "search_text, metadata, input, output) "
                "VALUES (:id, :project_id, :name, :status, :model, :started_at, "
                ":ended_at, :latency_ms, :cost, :tokens_in, :tokens_out, :search_text, "
                "CAST(:metadata AS jsonb), CAST(:input AS jsonb), CAST(:output AS jsonb))"
            ),
            {
                "id": trace_id,
                "project_id": uuid.UUID(project_id),
                "name": name,
                "status": status,
                "model": model,
                "started_at": started,
                "ended_at": started + timedelta(milliseconds=latency_ms or 0),
                "latency_ms": latency_ms,
                "cost": cost,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "search_text": search_text if search_text is not None else name.lower(),
                "metadata": metadata,
                "input": '{"question": "hello"}',
                "output": '{"answer": "hi"}',
            },
        )
        for index in range(spans):
            await conn.execute(
                text(
                    "INSERT INTO spans (id, trace_id, project_id, name, type, status, "
                    "started_at, ended_at) VALUES (:id, :trace_id, :project_id, :name, "
                    "'llm', 'ok', :started_at, :ended_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "trace_id": trace_id,
                    "project_id": uuid.UUID(project_id),
                    "name": f"span {index}",
                    "started_at": started,
                    "ended_at": started + timedelta(milliseconds=10),
                },
            )
    return str(trace_id)


# ── listing, filtering, pagination ──────────────────────────────────────

async def test_list_returns_newest_first(tenant):
    old = await insert_trace(tenant.project_id, name="older", started_at=ago(hours=2))
    new = await insert_trace(tenant.project_id, name="newer", started_at=ago(minutes=1))

    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces")

    assert res.status_code == 200
    assert [t["id"] for t in res.json()["items"]] == [new, old]


async def test_filters_narrow_the_result_set(tenant):
    await insert_trace(tenant.project_id, name="checkout", status="ok", model="gpt-4o")
    await insert_trace(tenant.project_id, name="checkout", status="error", model="gpt-4o")
    await insert_trace(tenant.project_id, name="search", status="ok", model="claude-sonnet-5")
    base = f"/v1/projects/{tenant.project_id}/traces"

    async def names(query: str) -> list[str]:
        res = await tenant.client.get(f"{base}?{query}")
        return [t["name"] for t in res.json()["items"]]

    assert sorted(await names("name=checkout")) == ["checkout", "checkout"]
    assert await names("status=error") == ["checkout"]
    assert await names("model=claude-sonnet-5") == ["search"]
    assert await names("status=ok&model=gpt-4o") == ["checkout"]


async def test_metadata_filter_matches_by_containment(tenant):
    await insert_trace(tenant.project_id, name="pro user", metadata='{"tier": "pro"}')
    await insert_trace(tenant.project_id, name="free user", metadata='{"tier": "free"}')

    res = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/traces?metadata.tier=pro"
    )
    items = res.json()["items"]
    assert [t["name"] for t in items] == ["pro user"]
    assert items[0]["metadata"]["tier"] == "pro"


async def test_time_range_filters_are_inclusive_of_the_window(tenant):
    await insert_trace(tenant.project_id, name="ancient", started_at=ago(days=10))
    await insert_trace(tenant.project_id, name="recent", started_at=ago(hours=1))

    # params= so httpx percent-encodes the `+00:00` offset for us.
    res = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/traces",
        params={"since": ago(days=1).isoformat()},
    )
    assert [t["name"] for t in res.json()["items"]] == ["recent"]


async def test_cursor_pagination_walks_every_row_exactly_once(tenant):
    created = [
        await insert_trace(tenant.project_id, started_at=ago(minutes=i)) for i in range(1, 8)
    ]

    seen, cursor, pages = [], None, 0
    while True:
        url = f"/v1/projects/{tenant.project_id}/traces?limit=3"
        if cursor:
            url += f"&cursor={cursor}"
        body = (await tenant.client.get(url)).json()
        seen.extend(t["id"] for t in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert pages == 3
    assert seen == sorted(created, key=created.index)  # newest-first order preserved
    assert len(set(seen)) == len(created)  # no row served twice or skipped


async def test_last_page_reports_no_cursor(tenant):
    await insert_trace(tenant.project_id)
    body = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces")).json()
    assert body["next_cursor"] is None


async def test_malformed_cursor_is_rejected(tenant):
    res = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/traces?cursor=not-a-real-cursor"
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "invalid_cursor"


async def test_page_size_is_capped(tenant):
    for _ in range(3):
        await insert_trace(tenant.project_id)
    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces?limit=10000")
    assert res.status_code == 200  # clamped server-side, not rejected


# ── free-text search fences (checklist row 26) ──────────────────────────

async def test_search_matches_the_search_text_projection(tenant):
    await insert_trace(
        tenant.project_id, name="billing question",
        search_text="billing question how do i cancel my subscription",
    )
    await insert_trace(tenant.project_id, name="weather", search_text="weather in paris")

    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces?q=cancel")
    assert [t["name"] for t in res.json()["items"]] == ["billing question"]


async def test_search_is_case_insensitive(tenant):
    await insert_trace(tenant.project_id, name="Refunds", search_text="refund policy")
    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces?q=REFUND")
    assert len(res.json()["items"]) == 1


async def test_search_below_the_trigram_floor_is_rejected(tenant):
    """Under 3 characters a trigram index can't help, so the query is refused
    rather than silently seq-scanning."""
    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces?q=ab")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "query_too_short"


async def test_search_is_bounded_to_the_retention_window(tenant):
    """Free text carries a server-enforced time bound: a client asking for all
    of history gets the maximum window, not an unbounded scan."""
    await insert_trace(
        tenant.project_id, name="ancient", search_text="ancient mystery",
        started_at=ago(days=90),
    )
    res = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/traces",
        params={"q": "ancient", "since": ago(days=365).isoformat()},
    )
    assert res.json()["items"] == []


# ── detail ──────────────────────────────────────────────────────────────

async def test_detail_returns_payloads_and_spans(tenant):
    trace_id = await insert_trace(tenant.project_id, spans=3)

    res = await tenant.client.get(f"/v1/traces/{trace_id}")

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == trace_id
    assert body["input"] == {"question": "hello"}
    assert len(body["spans"]) == 3
    assert all(s["trace_id"] == trace_id for s in body["spans"])


async def test_unknown_trace_404s(tenant):
    res = await tenant.client.get(f"/v1/traces/{uuid.uuid4()}")
    assert res.status_code == 404


# ── tenant scoping ──────────────────────────────────────────────────────

async def test_another_tenants_traces_are_invisible_and_404(tenant_factory):
    acme = await tenant_factory("Acme")
    globex = await tenant_factory("Globex")
    acme_trace = await insert_trace(acme.project_id, name="acme secret")

    # Not listed under the other tenant's project…
    listed = await globex.client.get(f"/v1/projects/{globex.project_id}/traces")
    assert listed.json()["items"] == []

    # …not readable by ID, and the project itself 404s rather than 403s.
    assert (await globex.client.get(f"/v1/traces/{acme_trace}")).status_code == 404
    assert (
        await globex.client.get(f"/v1/projects/{acme.project_id}/traces")
    ).status_code == 404
    assert (
        await globex.client.get(f"/v1/projects/{acme.project_id}/metrics/overview")
    ).status_code == 404


async def test_read_apis_require_a_session(client, tenant):
    assert (await client.get(f"/v1/projects/{tenant.project_id}/traces")).status_code == 401


# ── metrics overview ────────────────────────────────────────────────────

async def test_overview_aggregates_counts_cost_and_latency(tenant):
    for latency in (100, 200, 300, 400):
        await insert_trace(tenant.project_id, latency_ms=latency, cost=0.01)
    await insert_trace(tenant.project_id, status="error", latency_ms=500, cost=0.01)

    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/metrics/overview")

    body = res.json()
    assert body["total_traces"] == 5
    assert body["error_rate"] == 0.2
    assert body["p50_latency_ms"] == 300
    assert body["total_cost_usd"] == 0.05
    assert body["total_tokens_in"] == 500
    assert body["interval"] == "hour"


async def test_overview_buckets_by_the_requested_interval(tenant):
    await insert_trace(tenant.project_id, started_at=ago(hours=1))
    await insert_trace(tenant.project_id, started_at=ago(hours=1))
    await insert_trace(tenant.project_id, started_at=ago(hours=30))

    hourly = (
        await tenant.client.get(
            f"/v1/projects/{tenant.project_id}/metrics/overview?interval=hour"
        )
    ).json()
    daily = (
        await tenant.client.get(
            f"/v1/projects/{tenant.project_id}/metrics/overview?interval=day"
        )
    ).json()

    assert len(hourly["buckets"]) == 2
    assert sum(b["traces"] for b in hourly["buckets"]) == 3
    assert len(daily["buckets"]) == 2
    assert daily["interval"] == "day"


async def test_overview_on_an_empty_project_is_zeroed_not_an_error(tenant):
    res = await tenant.client.get(f"/v1/projects/{tenant.project_id}/metrics/overview")
    body = res.json()
    assert body["total_traces"] == 0
    assert body["error_rate"] == 0.0
    assert body["p50_latency_ms"] is None
    assert body["buckets"] == []


async def test_overview_rejects_an_unknown_interval(tenant):
    res = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/metrics/overview?interval=week"
    )
    assert res.status_code == 422
