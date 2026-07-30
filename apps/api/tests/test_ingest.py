"""Ingest endpoint (plan/ingestion.md §1–2).

The endpoint's contract: validate hard, repair what's repairable, and never
write to Postgres on the request path — its only durable act is the XADD.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from anvil_api.config import get_settings
from anvil_api.redis_client import get_redis
from anvil_api.services.normalize import TRUNCATED_FLAG
from anvil_api.streams import TRACES_STREAM

from .conftest import requires_services

pytestmark = requires_services


def utc(offset_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


def make_batch(count: int = 1, spans_per_trace: int = 1, **trace_overrides) -> dict:
    traces = []
    for _ in range(count):
        trace = {
            "id": str(uuid.uuid4()),
            "name": "rag query",
            "status": "ok",
            "input": {"question": "what is anvil"},
            "output": {"answer": "observability"},
            "started_at": utc(0),
            "ended_at": utc(2),
            "spans": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "llm call",
                    "type": "llm",
                    "started_at": utc(0),
                    "ended_at": utc(1),
                    "tokens_in": 100,
                    "tokens_out": 50,
                }
                for _ in range(spans_per_trace)
            ],
        }
        trace.update(trace_overrides)
        traces.append(trace)
    return {"traces": traces}


async def _stream_payloads() -> list[dict]:
    entries = await get_redis().xrange(TRACES_STREAM)
    return [json.loads(fields["payload"]) for _id, fields in entries]


async def test_valid_batch_is_accepted_and_queued(client, tenant):
    batch = make_batch(count=3)
    res = await client.post("/v1/ingest/traces", json=batch, headers=tenant.headers)

    assert res.status_code == 202
    assert res.json() == {"accepted": 3}

    queued = await _stream_payloads()
    assert len(queued) == 3
    assert {t["id"] for t in queued} == {t["id"] for t in batch["traces"]}


async def test_project_id_comes_from_the_key_never_the_payload(client, tenant):
    """Payload tenancy is never trusted (plan.md).

    The wire schema sets `additionalProperties: false`, so a client can't even
    express a `project_id` — the field is refused rather than quietly
    overwritten, and the value on the stream always comes from the key.
    """
    forged = await client.post(
        "/v1/ingest/traces",
        json=make_batch(project_id=str(uuid.uuid4())),
        headers=tenant.headers,
    )
    assert forged.status_code == 422

    accepted = await client.post(
        "/v1/ingest/traces", json=make_batch(), headers=tenant.headers
    )
    assert accepted.status_code == 202

    queued = await _stream_payloads()
    assert queued[0]["project_id"] == tenant.project_id


async def test_malformed_batch_is_rejected_with_a_usable_error(client, tenant):
    batch = make_batch()
    batch["traces"][0]["spans"][0]["type"] = "not-a-span-type"

    res = await client.post("/v1/ingest/traces", json=batch, headers=tenant.headers)

    assert res.status_code == 422
    detail = res.json()["error"]
    assert detail["code"] == "invalid_payload"
    # The path is what lets an SDK author find the field without guessing. The
    # envelope carries a router's extra keys through rather than flattening them.
    assert "spans" in detail["errors"][0]["path"]
    assert detail["errors"][0]["trace_index"] == 0
    assert await get_redis().xlen(TRACES_STREAM) == 0


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b["traces"][0].pop("id"), id="missing-id"),
        pytest.param(lambda b: b["traces"][0].update(id="not-a-uuid"), id="bad-uuid"),
        pytest.param(lambda b: b["traces"][0].pop("started_at"), id="missing-started-at"),
        pytest.param(lambda b: b.update(traces=[]), id="empty-batch"),
        pytest.param(lambda b: b["traces"][0].update(surprise=1), id="unknown-field"),
    ],
)
async def test_schema_violations_are_422(client, tenant, mutate):
    batch = make_batch()
    mutate(batch)
    res = await client.post("/v1/ingest/traces", json=batch, headers=tenant.headers)
    assert res.status_code == 422


async def test_body_over_the_size_cap_is_413(client, tenant, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_max_body_bytes", 2048)

    res = await client.post(
        "/v1/ingest/traces",
        json=make_batch(input={"blob": "x" * 10_000}),
        headers=tenant.headers,
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


async def test_oversized_field_is_truncated_rather_than_rejected(
    client, tenant, monkeypatch
):
    """Checklist row 6: never reject a whole batch for oversized content."""
    monkeypatch.setattr(get_settings(), "ingest_max_field_bytes", 512)

    res = await client.post(
        "/v1/ingest/traces",
        json=make_batch(output={"answer": "z" * 5000}),
        headers=tenant.headers,
    )
    assert res.status_code == 202

    queued = await _stream_payloads()
    assert queued[0]["output"][TRUNCATED_FLAG] is True
    assert queued[0]["metadata"][TRUNCATED_FLAG] is True


async def test_invalid_json_is_400(client, tenant):
    res = await client.post(
        "/v1/ingest/traces",
        content=b"{not json",
        headers={**tenant.headers, "content-type": "application/json"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_json"


async def test_ingest_requires_a_live_api_key(client, tenant):
    assert (await client.post("/v1/ingest/traces", json=make_batch())).status_code == 401

    await tenant.client.delete(f"/v1/api-keys/{tenant.key_id}")
    revoked = await client.post(
        "/v1/ingest/traces", json=make_batch(), headers=tenant.headers
    )
    assert revoked.status_code == 401


async def test_backpressure_returns_429_when_the_buffer_is_saturated(
    client, tenant, monkeypatch
):
    """Global backpressure protects Anvil itself; it is separate from the
    per-key limit and carries a different code so SDKs can tell them apart."""
    await client.post("/v1/ingest/traces", json=make_batch(), headers=tenant.headers)
    monkeypatch.setattr(get_settings(), "ingest_high_water", 0)

    res = await client.post("/v1/ingest/traces", json=make_batch(), headers=tenant.headers)

    assert res.status_code == 429
    assert res.json()["error"]["code"] == "backpressure"
    assert res.headers["Retry-After"]


async def test_noisy_key_is_throttled_without_affecting_another_key(
    client, tenant_factory, monkeypatch
):
    """Checklist row 16: one buggy client must not starve other tenants."""
    monkeypatch.setattr(get_settings(), "ingest_rate_limit_rpm", 3)
    noisy = await tenant_factory("Noisy")
    quiet = await tenant_factory("Quiet")

    statuses = [
        (await client.post("/v1/ingest/traces", json=make_batch(), headers=noisy.headers)).status_code
        for _ in range(4)
    ]
    assert statuses[:3] == [202, 202, 202]
    assert statuses[3] == 429

    unaffected = await client.post(
        "/v1/ingest/traces", json=make_batch(), headers=quiet.headers
    )
    assert unaffected.status_code == 202


async def test_trace_count_limit_bills_per_trace_not_per_request(
    client, tenant, monkeypatch
):
    """A single large batch can exhaust the traces/min budget on its own."""
    monkeypatch.setattr(get_settings(), "ingest_rate_limit_traces_pm", 5)

    first = await client.post(
        "/v1/ingest/traces", json=make_batch(count=5), headers=tenant.headers
    )
    assert first.status_code == 202

    second = await client.post("/v1/ingest/traces", json=make_batch(), headers=tenant.headers)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"


async def test_stats_reports_accepts_rejects_and_depth(client, tenant):
    await client.post("/v1/ingest/traces", json=make_batch(count=2), headers=tenant.headers)
    bad = make_batch()
    bad["traces"][0]["spans"][0]["type"] = "nope"
    await client.post("/v1/ingest/traces", json=bad, headers=tenant.headers)

    res = await client.get("/v1/ingest/stats", headers=tenant.headers)

    assert res.status_code == 200
    body = res.json()
    assert body["counters"]["accepted"] == 1
    assert body["counters"]["invalid"] == 1
    assert body["stream_depth"] == 2
    assert body["key_prefix"].startswith("ak_")
