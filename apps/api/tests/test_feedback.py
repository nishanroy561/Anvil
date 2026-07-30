"""Feedback submission, the trace filter, and the failure inbox (Phase 9,
plan/evaluation.md §7).

The endpoint's contract is deliberately narrow: it queues, it does not write. So
these tests assert on the *stream*, not on rows — anything that checks Postgres
after a POST would be testing the worker, which has its own suite.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from anvil_api.db import engine
from anvil_api.redis_client import get_redis
from anvil_api.streams import FEEDBACK_STREAM

from .conftest import requires_services

pytestmark = requires_services


def ago(**kwargs) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


async def insert_trace(project_id: str, *, name: str = "ask", minutes: int = 5) -> str:
    trace_id = uuid.uuid4()
    started = ago(minutes=minutes)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO traces (id, project_id, name, status, started_at, ended_at) "
                "VALUES (:id, :project_id, :name, 'ok', :started_at, :ended_at)"
            ),
            {
                "id": trace_id,
                "project_id": uuid.UUID(project_id),
                "name": name,
                "started_at": started,
                "ended_at": started + timedelta(seconds=1),
            },
        )
    return str(trace_id)


async def insert_feedback(
    project_id: str,
    trace_id: str,
    *,
    rating: int = -1,
    comment: str | None = "wrong answer",
    user_ref: str | None = None,
    minutes: int = 1,
) -> str:
    feedback_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feedback (id, project_id, trace_id, rating, comment, "
                "user_ref, created_at) VALUES (:id, :project_id, :trace_id, :rating, "
                ":comment, :user_ref, :created_at)"
            ),
            {
                "id": feedback_id,
                "project_id": uuid.UUID(project_id),
                "trace_id": uuid.UUID(trace_id),
                "rating": rating,
                "comment": comment,
                "user_ref": user_ref,
                "created_at": ago(minutes=minutes),
            },
        )
    return str(feedback_id)


async def stream_events() -> list[dict]:
    entries = await get_redis().xrange(FEEDBACK_STREAM)
    return [json.loads(fields["payload"]) for _id, fields in entries]


# ---- submission -------------------------------------------------------------


async def test_feedback_is_queued_not_written(client, tenant):
    """202 and a stream entry. The row does not exist yet, and that is the
    design: the trace it points at may not exist yet either."""
    trace_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/feedback",
        json={"trace_id": trace_id, "rating": -1, "comment": "hallucinated"},
        headers=tenant.headers,
    )
    assert response.status_code == 202
    assert uuid.UUID(response.json()["id"])

    events = await stream_events()
    assert len(events) == 1
    assert events[0]["trace_id"] == trace_id
    assert events[0]["rating"] == -1
    assert events[0]["comment"] == "hallucinated"
    # Stamped from the key, and present so the worker can bound its retries.
    assert events[0]["project_id"] == tenant.project_id
    assert events[0]["enqueued_at"]

    async with engine.begin() as conn:
        rows = await conn.exec_driver_sql("SELECT count(*) FROM feedback")
    assert rows.scalar() == 0


async def test_feedback_id_is_server_minted_and_echoed(client, tenant):
    """The id in the 202 is the id the row will get. It has to come from here:
    anonymous feedback has no unique key of its own, so a redelivered stream
    entry would otherwise insert a second row."""
    trace_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/feedback", json={"trace_id": trace_id, "rating": 1}, headers=tenant.headers
    )
    assert response.json()["id"] == (await stream_events())[0]["id"]


async def test_project_id_cannot_be_sent_in_the_body(client, tenant):
    """Tenancy comes from the credential, never the payload. `extra="forbid"`
    makes it unexpressible rather than merely ignored."""
    response = await client.post(
        "/v1/feedback",
        json={
            "trace_id": str(uuid.uuid4()),
            "rating": -1,
            "project_id": str(uuid.uuid4()),
        },
        headers=tenant.headers,
    )
    assert response.status_code == 422
    assert await stream_events() == []


async def test_neutral_rating_is_refused(client, tenant):
    """A `0` would file feedback no filter surfaces and no inbox shows."""
    for rating in (0, 2, -5, "down"):
        response = await client.post(
            "/v1/feedback",
            json={"trace_id": str(uuid.uuid4()), "rating": rating},
            headers=tenant.headers,
        )
        assert response.status_code == 422, rating
    assert await stream_events() == []


async def test_long_comment_is_truncated_not_refused(client, tenant):
    """The rating is the signal worth keeping; a 422 would throw away a real
    thumbs-down over a long essay."""
    response = await client.post(
        "/v1/feedback",
        json={"trace_id": str(uuid.uuid4()), "rating": -1, "comment": "x" * 9000},
        headers=tenant.headers,
    )
    assert response.status_code == 202
    comment = (await stream_events())[0]["comment"]
    assert len(comment) < 9000
    assert comment.endswith("…[truncated]")


async def test_feedback_requires_an_api_key(client, tenant):
    assert (
        await client.post(
            "/v1/feedback", json={"trace_id": str(uuid.uuid4()), "rating": -1}
        )
    ).status_code == 401

    revoke = await tenant.client.delete(f"/v1/api-keys/{tenant.key_id}")
    assert revoke.status_code in (200, 204)
    assert (
        await client.post(
            "/v1/feedback",
            json={"trace_id": str(uuid.uuid4()), "rating": -1},
            headers=tenant.headers,
        )
    ).status_code == 401


async def test_feedback_is_rate_limited_per_key(client, tenant, monkeypatch):
    """The most abuse-exposed surface after ingest — it is reachable from a
    customer's own end users (checklist row 19)."""
    from anvil_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "feedback_rate_limit_pm", 3, raising=False)

    body = {"trace_id": str(uuid.uuid4()), "rating": -1}
    codes = [
        (await client.post("/v1/feedback", json=body, headers=tenant.headers)).status_code
        for _ in range(5)
    ]
    assert codes == [202, 202, 202, 429, 429]

    limited = await client.post("/v1/feedback", json=body, headers=tenant.headers)
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert limited.headers["Retry-After"]


async def test_one_keys_limit_does_not_throttle_another(client, tenant_factory, monkeypatch):
    from anvil_api.config import get_settings

    monkeypatch.setattr(get_settings(), "feedback_rate_limit_pm", 2, raising=False)
    noisy = await tenant_factory("Noisy")
    quiet = await tenant_factory("Quiet")

    body = {"trace_id": str(uuid.uuid4()), "rating": -1}
    for _ in range(4):
        await client.post("/v1/feedback", json=body, headers=noisy.headers)

    assert (
        await client.post("/v1/feedback", json=body, headers=quiet.headers)
    ).status_code == 202


# ---- read side --------------------------------------------------------------


async def test_trace_list_carries_feedback_counts(tenant):
    trace_id = await insert_trace(tenant.project_id)
    await insert_feedback(tenant.project_id, trace_id, rating=-1, user_ref="u1")
    await insert_feedback(tenant.project_id, trace_id, rating=-1, user_ref="u2")
    await insert_feedback(tenant.project_id, trace_id, rating=1, user_ref="u3")

    page = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces")
    ).json()
    # One row, not three: feedback must not multiply the trace (that would
    # silently break the keyset page size as well as the display).
    assert len(page["items"]) == 1
    assert page["items"][0]["feedback"] == {"negative": 2, "positive": 1}


async def test_trace_without_feedback_reports_zeroes(tenant):
    await insert_trace(tenant.project_id)
    page = (await tenant.client.get(f"/v1/projects/{tenant.project_id}/traces")).json()
    assert page["items"][0]["feedback"] == {"negative": 0, "positive": 0}


async def test_feedback_filter(tenant):
    downvoted = await insert_trace(tenant.project_id, name="bad")
    upvoted = await insert_trace(tenant.project_id, name="good")
    await insert_trace(tenant.project_id, name="unrated")
    await insert_feedback(tenant.project_id, downvoted, rating=-1)
    await insert_feedback(tenant.project_id, upvoted, rating=1)

    async def names(query: str) -> set[str]:
        page = (
            await tenant.client.get(
                f"/v1/projects/{tenant.project_id}/traces?feedback={query}"
            )
        ).json()
        return {item["name"] for item in page["items"]}

    assert await names("negative") == {"bad"}
    assert await names("positive") == {"good"}
    assert await names("any") == {"bad", "good"}
    assert await names("none") == {"unrated"}


async def test_unknown_feedback_filter_is_rejected(tenant):
    response = await tenant.client.get(
        f"/v1/projects/{tenant.project_id}/traces?feedback=maybe"
    )
    # A typo must not silently return everything unfiltered.
    assert response.status_code == 422


async def test_trace_detail_carries_comments(tenant):
    trace_id = await insert_trace(tenant.project_id)
    await insert_feedback(
        tenant.project_id, trace_id, rating=-1, comment="made up a date", user_ref="u1"
    )
    detail = (await tenant.client.get(f"/v1/traces/{trace_id}")).json()
    assert detail["feedback"] == {"negative": 1, "positive": 0}
    assert len(detail["feedback_events"]) == 1
    assert detail["feedback_events"][0]["comment"] == "made up a date"
    assert detail["feedback_events"][0]["user_ref"] == "u1"


# ---- failure inbox ----------------------------------------------------------


async def test_inbox_lists_downvoted_traces_not_yet_in_a_dataset(tenant):
    downvoted = await insert_trace(tenant.project_id, name="bad")
    await insert_feedback(tenant.project_id, downvoted, rating=-1, comment="nope")

    upvoted = await insert_trace(tenant.project_id, name="good")
    await insert_feedback(tenant.project_id, upvoted, rating=1)

    await insert_trace(tenant.project_id, name="unrated")

    inbox = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/failure-inbox")
    ).json()
    assert inbox["total"] == 1
    assert [i["trace"]["name"] for i in inbox["items"]] == ["bad"]
    assert inbox["items"][0]["feedback"][0]["comment"] == "nope"
    assert inbox["items"][0]["latest_feedback_at"]


async def test_promoting_clears_the_inbox(tenant):
    """Promotion is what drains the queue — that is the difference between an
    inbox and a list."""
    trace_id = await insert_trace(tenant.project_id, name="bad")
    await insert_feedback(tenant.project_id, trace_id, rating=-1)

    dataset = (
        await tenant.client.post(
            f"/v1/projects/{tenant.project_id}/datasets", json={"name": "failures"}
        )
    ).json()
    promote = await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]}
    )
    assert promote.json()["promoted"] == 1

    inbox = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/failure-inbox")
    ).json()
    assert inbox == {"items": [], "total": 0}


async def test_inbox_groups_by_trace(tenant):
    """Four downvotes on one trace is one thing to triage, not four."""
    trace_id = await insert_trace(tenant.project_id)
    for i in range(4):
        await insert_feedback(
            tenant.project_id, trace_id, rating=-1, user_ref=f"u{i}", minutes=i + 1
        )

    inbox = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/failure-inbox")
    ).json()
    assert inbox["total"] == 1
    assert len(inbox["items"]) == 1
    assert len(inbox["items"][0]["feedback"]) == 4
    assert inbox["items"][0]["trace"]["feedback"]["negative"] == 4


async def test_inbox_is_newest_first_and_paginates(tenant):
    for i in range(3):
        trace_id = await insert_trace(tenant.project_id, name=f"t{i}")
        await insert_feedback(tenant.project_id, trace_id, rating=-1, minutes=10 - i)

    first = (
        await tenant.client.get(
            f"/v1/projects/{tenant.project_id}/failure-inbox?limit=2"
        )
    ).json()
    assert [i["trace"]["name"] for i in first["items"]] == ["t2", "t1"]
    assert first["total"] == 3

    second = (
        await tenant.client.get(
            f"/v1/projects/{tenant.project_id}/failure-inbox?limit=2&offset=2"
        )
    ).json()
    assert [i["trace"]["name"] for i in second["items"]] == ["t0"]


async def test_overview_counts_votes_per_bucket(tenant):
    """The series has to line up with `traces`: bucketed on the trace's start
    time, and never multiplying the other aggregates."""
    downvoted = await insert_trace(tenant.project_id, minutes=5)
    await insert_feedback(tenant.project_id, downvoted, rating=-1, user_ref="u1")
    await insert_feedback(tenant.project_id, downvoted, rating=-1, user_ref="u2")
    await insert_feedback(tenant.project_id, downvoted, rating=1, user_ref="u3")
    await insert_trace(tenant.project_id, minutes=5)

    overview = (
        await tenant.client.get(
            f"/v1/projects/{tenant.project_id}/metrics/overview?interval=hour"
        )
    ).json()

    assert overview["total_traces"] == 2, "three votes must not inflate the trace count"
    assert overview["total_feedback_negative"] == 2
    assert overview["total_feedback_positive"] == 1
    votes = [(b["feedback_negative"], b["feedback_positive"]) for b in overview["buckets"]]
    assert (2, 1) in votes


async def test_overview_buckets_without_votes_report_zero(tenant):
    await insert_trace(tenant.project_id)
    overview = (
        await tenant.client.get(f"/v1/projects/{tenant.project_id}/metrics/overview")
    ).json()
    assert overview["total_feedback_negative"] == 0
    assert all(b["feedback_negative"] == 0 for b in overview["buckets"])


async def test_inbox_and_filter_are_tenant_scoped(tenant_factory):
    mine = await tenant_factory("Mine")
    theirs = await tenant_factory("Theirs")

    trace_id = await insert_trace(theirs.project_id, name="their failure")
    await insert_feedback(theirs.project_id, trace_id, rating=-1)

    # Another tenant's project is indistinguishable from one that doesn't exist.
    assert (
        await mine.client.get(f"/v1/projects/{theirs.project_id}/failure-inbox")
    ).status_code == 404

    inbox = (
        await mine.client.get(f"/v1/projects/{mine.project_id}/failure-inbox")
    ).json()
    assert inbox["total"] == 0
