"""Monitor and alert-rule APIs (plan/evaluation.md §6).

Tenancy and the two validations that stop a monitor from failing silently: an
unparseable cron (never runs, looks healthy) and a bad grader config (scores
everything 1.0, looks healthy).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from anvil_api.db import engine

from .conftest import requires_services

pytestmark = requires_services


def monitor_body(**overrides):
    body = {
        "name": "answer quality",
        "sample_rate": 0.25,
        "schedule_cron": "*/10 * * * *",
        "graders": [{"name": "valid_json", "config": {}}],
    }
    body.update(overrides)
    return body


def rule_body(**overrides):
    body = {
        "name": "error rate",
        "metric": "error_rate",
        "comparison": "gt",
        "threshold": 0.1,
        "window_minutes": 15,
    }
    body.update(overrides)
    return body


async def make_monitor(tenant, **overrides):
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/monitors", json=monitor_body(**overrides)
    )
    assert res.status_code == 201, res.text
    return res.json()


async def make_rule(tenant, **overrides):
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/alert-rules", json=rule_body(**overrides)
    )
    assert res.status_code == 201, res.text
    return res.json()


async def insert_trace(project_id: str) -> str:
    trace_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, started_at) "
                "VALUES (:id, :pid, 'ask', 'ok', now())"
            ),
            {"id": trace_id, "pid": project_id},
        )
    return str(trace_id)


async def insert_score(monitor_id: str, trace_id: str, scores: dict, days_ago: int = 0):
    # The timestamp is computed here rather than as `now() - interval` in SQL:
    # asyncpg binds an interval from a timedelta, not a string, and passing a
    # ready datetime keeps the helper obvious.
    created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO monitor_scores (id, monitor_id, trace_id, scores, created_at) "
                "VALUES (:id, :mid, :tid, CAST(:s AS jsonb), :created_at)"
            ),
            {
                "id": uuid.uuid4(),
                "mid": monitor_id,
                "tid": trace_id,
                "s": json.dumps(scores),
                "created_at": created_at,
            },
        )


# ---- monitor CRUD -----------------------------------------------------------


async def test_create_and_list_monitors(tenant):
    created = await make_monitor(tenant)
    assert created["sample_rate"] == 0.25
    assert created["grader_config"]["graders"][0]["name"] == "valid_json"
    assert created["last_run_at"] is None

    listed = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/monitors")).json()
    assert [m["id"] for m in listed] == [created["id"]]


async def test_update_and_disable_a_monitor(tenant):
    monitor = await make_monitor(tenant)
    res = await tenant.client.patch(
        f"/v1/monitors/{monitor['id']}", json={"enabled": False, "sample_rate": 1.0}
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is False
    assert res.json()["sample_rate"] == 1.0


async def test_delete_a_monitor_removes_its_scores(tenant):
    monitor = await make_monitor(tenant)
    trace_id = await insert_trace(tenant.project_id)
    await insert_score(monitor["id"], trace_id, {"valid_json": {"score": 1.0}})

    assert (await tenant.client.delete(f"/v1/monitors/{monitor['id']}")).status_code == 204
    assert (await tenant.client.get(f"/v1/monitors/{monitor['id']}")).status_code == 404


async def test_an_invalid_cron_is_refused(tenant):
    """A monitor with an unparseable schedule never runs and looks healthy
    doing it — the failure mode is indistinguishable from 'nothing is wrong'."""
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/monitors",
        json=monitor_body(schedule_cron="every ten minutes please"),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_cron"


async def test_an_invalid_grader_config_is_refused(tenant):
    """Same class of silent failure as the eval path: `contains` with no value
    matches every string and scores a confident 1.0 forever."""
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/monitors",
        json=monitor_body(graders=[{"name": "contains", "config": {}}]),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_grader_config"


async def test_sample_rate_is_bounded(tenant):
    for bad in (-0.1, 1.5):
        res = await tenant.client.post(
            f"/v1/projects/{tenant.project_id}/monitors", json=monitor_body(sample_rate=bad)
        )
        assert res.status_code == 422


async def test_a_zero_sample_rate_is_allowed(tenant):
    """A way to pause a monitor without losing its configuration."""
    monitor = await make_monitor(tenant, sample_rate=0.0)
    assert monitor["sample_rate"] == 0.0


# ---- drift series -----------------------------------------------------------


async def test_the_score_series_buckets_by_day(tenant):
    monitor = await make_monitor(tenant)
    for days_ago, score in ((2, 1.0), (2, 0.0), (0, 1.0)):
        await insert_score(monitor["id"], await insert_trace(tenant.project_id),
                           {"valid_json": {"score": score}}, days_ago=days_ago)

    body = (await tenant.client.get(f"/v1/monitors/{monitor['id']}/scores")).json()

    assert body["graders"] == ["valid_json"]
    assert len(body["buckets"]) == 2
    assert body["buckets"][0]["mean_score"] == 0.5
    assert body["buckets"][0]["samples"] == 2
    assert body["buckets"][-1]["mean_score"] == 1.0


async def test_a_grader_error_is_excluded_from_the_series(tenant):
    """A broken judge must not read as a quality collapse on the chart."""
    monitor = await make_monitor(tenant)
    await insert_score(monitor["id"], await insert_trace(tenant.project_id),
                       {"j": {"score": 1.0}})
    await insert_score(monitor["id"], await insert_trace(tenant.project_id),
                       {"j": {"error": "provider 429"}})

    body = (await tenant.client.get(f"/v1/monitors/{monitor['id']}/scores")).json()
    assert body["buckets"][-1]["mean_score"] == 1.0
    assert body["buckets"][-1]["samples"] == 1


async def test_recent_samples_are_listed_newest_first(tenant):
    monitor = await make_monitor(tenant)
    for days_ago in (3, 1, 0):
        await insert_score(monitor["id"], await insert_trace(tenant.project_id),
                           {"valid_json": {"score": 1.0}}, days_ago=days_ago)

    samples = (await tenant.client.get(f"/v1/monitors/{monitor['id']}/samples")).json()
    assert len(samples) == 3
    assert samples[0]["created_at"] > samples[-1]["created_at"]
    assert samples[0]["trace_id"]  # links through to the trace page


# ---- alert rules and events -------------------------------------------------


async def test_create_and_list_alert_rules(tenant):
    rule = await make_rule(tenant)
    assert rule["metric"] == "error_rate"
    listed = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/alert-rules")).json()
    assert [r["id"] for r in listed] == [rule["id"]]


async def test_an_unknown_metric_is_refused(tenant):
    """A rule the worker cannot evaluate would sit there looking enabled."""
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/alert-rules", json=rule_body(metric="vibes")
    )
    assert res.status_code == 422


async def test_an_unknown_comparison_is_refused(tenant):
    res = await tenant.client.post(
        f"/v1/projects/{tenant.project_id}/alert-rules", json=rule_body(comparison="sideways")
    )
    assert res.status_code == 422


async def test_alert_events_carry_the_rule_name(tenant):
    """An event id alone tells a reader nothing about what fired."""
    rule = await make_rule(tenant)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, rule_id, fired_at, value, notified) "
                "VALUES (:id, :rid, now(), 0.42, false)"
            ),
            {"id": uuid.uuid4(), "rid": rule["id"]},
        )

    events = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/alert-events")).json()
    assert len(events) == 1
    assert events[0]["rule_name"] == "error rate"
    assert events[0]["metric"] == "error_rate"
    assert events[0]["notified"] is False


async def test_active_filter_and_count(tenant):
    rule = await make_rule(tenant)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, rule_id, fired_at, value, notified) "
                "VALUES (:id, :rid, now(), 0.9, true)"
            ),
            {"id": uuid.uuid4(), "rid": rule["id"]},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, rule_id, fired_at, value, resolved_at, notified) "
                "VALUES (:id, :rid, now(), 0.9, now(), true)"
            ),
            {"id": uuid.uuid4(), "rid": rule["id"]},
        )

    active = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/alert-events?active=true")
    ).json()
    assert len(active) == 1
    count = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/alert-events/count")).json()
    assert count == {"active": 1}


# ---- webhook ----------------------------------------------------------------


async def test_setting_and_clearing_the_webhook(tenant):
    res = await tenant.client.patch(
        f"/v1/projects/{tenant.project_id}/alert-webhook",
        json={"alert_webhook_url": "https://hooks.example/anvil"},
    )
    assert res.status_code == 200
    assert res.json()["alert_webhook_url"] == "https://hooks.example/anvil"

    cleared = await tenant.client.patch(
        f"/v1/projects/{tenant.project_id}/alert-webhook", json={"alert_webhook_url": None}
    )
    assert cleared.json()["alert_webhook_url"] is None


async def test_a_non_http_webhook_is_refused(tenant):
    res = await tenant.client.patch(
        f"/v1/projects/{tenant.project_id}/alert-webhook",
        json={"alert_webhook_url": "hooks.example/anvil"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_webhook_url"


# ---- tenancy ----------------------------------------------------------------


async def test_cross_tenant_access_is_404_never_403(tenant_factory):
    mine, theirs = await tenant_factory(), await tenant_factory()
    monitor = await make_monitor(mine)
    rule = await make_rule(mine)

    assert (await theirs.client.get(f"/v1/monitors/{monitor['id']}")).status_code == 404
    assert (await theirs.client.patch(f"/v1/monitors/{monitor['id']}", json={"enabled": False})).status_code == 404
    assert (await theirs.client.delete(f"/v1/monitors/{monitor['id']}")).status_code == 404
    assert (await theirs.client.get(f"/v1/monitors/{monitor['id']}/scores")).status_code == 404
    assert (await theirs.client.get(f"/v1/monitors/{monitor['id']}/samples")).status_code == 404
    assert (await theirs.client.patch(f"/v1/alert-rules/{rule['id']}", json={"enabled": False})).status_code == 404
    assert (await theirs.client.delete(f"/v1/alert-rules/{rule['id']}")).status_code == 404
    assert (await theirs.client.get(f"/v1/projects/{mine.project_id}/monitors")).status_code == 404
    assert (await theirs.client.get(f"/v1/projects/{mine.project_id}/alert-events")).status_code == 404
    assert (
        await theirs.client.patch(
            f"/v1/projects/{mine.project_id}/alert-webhook",
            json={"alert_webhook_url": "https://evil.example"},
        )
    ).status_code == 404
