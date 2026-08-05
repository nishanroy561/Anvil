"""Phase 9 acceptance: thumbs-down -> failure inbox -> dataset -> eval run.

    docker compose up -d postgres redis
    cd apps/api && python -m uvicorn anvil_api.main:app --port 8000
    python scripts/feedback_acceptance.py        # exits 0 on pass

**The API runs live; the consumers run in-process.** Every request here goes over
real HTTP against a real server — the endpoint, the API-key auth, the rate limit
and the inbox SQL are all genuinely exercised. The ingest, feedback and eval
consumers are driven a cycle at a time from this process instead of from a
running worker, because the thing under test is a *race* and the deferred retry
clock is the mechanism: waiting out a real backoff would make the acceptance slow
and flaky without making it stronger. The code paths are the ones
`anvil_worker.main` schedules.

The order of the first two steps is the whole point. The vote is submitted for a
trace that **does not exist yet** — as it does in production, where the click
lands while the trace is still in the Redis buffer.

  1. vote before the trace   -> 202, deferred, zero rows written
  2. the trace lands         -> the deferred vote persists, exactly once
  3. failure inbox           -> the trace shows up with its comment
  4. list filter + detail    -> ?feedback=negative finds it, detail has the text
  5. toggle 👎 -> 👍 -> 👎    -> one row throughout, never two
  6. another tenant's vote   -> dropped, not written, not visible
  7. promote to a dataset    -> the inbox clears
  8. eval run                -> the promoted item is scored

No provider credits are spent: the grader is the deterministic `contains`.
"""

import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "worker"))

import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import select  # noqa: E402

from anvil_worker.config import get_settings  # noqa: E402
from anvil_worker.db import engine  # noqa: E402
from anvil_worker.eval_runner import EvalRunner  # noqa: E402
from anvil_worker.feedback_consumer import FeedbackConsumer  # noqa: E402
from anvil_worker.graders import load_graders  # noqa: E402
from anvil_worker.ingest_consumer import IngestConsumer  # noqa: E402
from anvil_worker.streams import FEEDBACK_DEFERRED_KEY  # noqa: E402
from anvil_worker.tables import eval_results, feedback as feedback_table  # noqa: E402

from _liveworker import refuse_if_worker_running  # noqa: E402
from _tenants import drop_tenant  # noqa: E402

BASE = "http://localhost:8000"
PASSWORD = "hunter2hunter2"

QUESTION = "How long do refunds take?"
BAD_ANSWER = "I'm sorry, I don't have that information available right now."
COMMENT = "this is wrong, refunds took three weeks"
USER = "end-user-42"


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---- live API helpers -------------------------------------------------------


# Every tenant this run provisions, so the teardown can remove them all even if
# an assertion fails before the second one is built. Without this the script left
# a `Phase 9 main` and a `Phase 9 other` org behind on every run — six of them had
# accumulated in the dev database before anyone looked.
CREATED_PROJECTS: list[str] = []
KEEP = "--keep" in sys.argv


def new_tenant(label: str) -> tuple[httpx.Client, str, str]:
    """Its own org, project and API key, so counts are not polluted by dev data.

    Returns (session client, project_id, api_key).
    """
    client = httpx.Client(base_url=BASE, timeout=30.0)
    email = f"phase9-{label}-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "org_name": f"Phase 9 {label}"},
    )
    signup.raise_for_status()
    project_id = client.get("/v1/projects").json()[0]["id"]
    key = client.post(
        f"/v1/projects/{project_id}/api-keys", json={"name": "acceptance"}
    ).json()["key"]
    CREATED_PROJECTS.append(project_id)
    return client, project_id, key


def submit_feedback(key: str, trace_id: str, rating: int, **extra) -> httpx.Response:
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        return c.post(
            "/v1/feedback",
            json={"trace_id": trace_id, "rating": rating, **extra},
            headers={"Authorization": f"Bearer {key}"},
        )


def ingest_trace(key: str, trace_id: str, *, output: str) -> httpx.Response:
    started = now() - timedelta(seconds=2)
    payload = {
        "traces": [
            {
                "id": trace_id,
                "name": "ask",
                "status": "ok",
                "input": {"question": QUESTION},
                "output": output,
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(seconds=1)).isoformat(),
                "spans": [],
            }
        ]
    }
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        return c.post(
            "/v1/ingest/traces",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )


# ---- database reads ---------------------------------------------------------


async def feedback_rows(trace_id: str) -> list[dict]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            select(feedback_table).where(feedback_table.c.trace_id == uuid.UUID(trace_id))
        )
        return [dict(r) for r in rows.mappings()]


async def result_rows(run_id: str) -> list[dict]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            select(eval_results).where(eval_results.c.run_id == uuid.UUID(run_id))
        )
        return [dict(r) for r in rows.mappings()]


async def make_due(redis) -> int:
    """Fast-forward the deferred set. The backoff clock is the mechanism under
    test, not the subject — sleeping through it would only make this slower."""
    members = await redis.zrange(FEEDBACK_DEFERRED_KEY, 0, -1)
    if members:
        await redis.zadd(FEEDBACK_DEFERRED_KEY, {m: 0 for m in members})
    return len(members)


async def main() -> int:
    load_graders()
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label} {detail}")

    try:
        with httpx.Client(base_url=BASE, timeout=5.0) as probe:
            health = probe.get("/healthz").json()
        if not health.get("db") or not health.get("redis"):
            print(f"API is up but unhealthy: {health}")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"cannot reach the API at {BASE}: {exc}")
        print("start it with: cd apps/api && python -m uvicorn anvil_api.main:app --port 8000")
        return 1

    # Before anything is provisioned: a live worker makes every count below zero
    # for reasons that have nothing to do with the code under test.
    from anvil_worker.streams import PERSISTERS_GROUP, TRACES_STREAM

    clash = await refuse_if_worker_running(
        redis, TRACES_STREAM, PERSISTERS_GROUP, {"acceptance-ingest"}
    )
    if clash:
        print(f"\n{clash}")
        return 2

    session, project_id, api_key = new_tenant("main")
    ingest = IngestConsumer(redis, consumer_name="acceptance-ingest")
    feedback = FeedbackConsumer(redis, consumer_name="acceptance-feedback")
    evals = EvalRunner(redis)
    await ingest.ensure_group()
    await feedback.ensure_group()

    # Client-generated, exactly as an SDK does it — which is what makes the race
    # possible at all: the id is known before the trace has been sent.
    trace_id = str(uuid.uuid4())

    try:
        print("\n=== 1. the vote arrives before the trace ===")
        response = submit_feedback(
            api_key, trace_id, -1, comment=COMMENT, user_ref=USER
        )
        check("POST /v1/feedback accepted", response.status_code == 202,
              f"{response.status_code} {response.text[:120]}")
        check("the response carries the row's future id",
              bool(response.status_code == 202 and response.json().get("id")))

        persisted = await feedback.process_stream(await feedback.read_batch())
        check("nothing was written — the trace does not exist yet", persisted == 0,
              f"persisted {persisted}")
        check("the vote is held, not refused",
              await redis.zcard(FEEDBACK_DEFERRED_KEY) == 1,
              f"deferred {await redis.zcard(FEEDBACK_DEFERRED_KEY)}")
        check("and it is not lost to the stream either", await feedback_rows(trace_id) == [])

        # A retry before the backoff elapses must be a no-op rather than a spin.
        check("a premature retry does nothing", await feedback.process_deferred() == 0)

        print("\n=== 2. the trace lands ===")
        accepted = ingest_trace(api_key, trace_id, output=BAD_ANSWER)
        check("trace ingested", accepted.status_code == 202, str(accepted.status_code))
        written, _spans = await ingest.persist(
            [json.loads(fields["payload"]) for _id, fields in await ingest.read_batch()]
        )
        check("trace persisted", written == 1, f"wrote {written}")

        await make_due(redis)
        persisted = await feedback.process_deferred()
        check("the deferred vote now persists", persisted == 1, f"persisted {persisted}")
        rows = await feedback_rows(trace_id)
        check("exactly one row", len(rows) == 1, f"{len(rows)} rows")
        check("with the user's comment intact",
              bool(rows and rows[0]["comment"] == COMMENT),
              rows[0]["comment"] if rows else "no row")
        check("the deferred set is empty", await redis.zcard(FEEDBACK_DEFERRED_KEY) == 0)

        # And it does not re-persist on every subsequent cycle.
        await feedback.process_deferred()
        check("a later cycle does not duplicate it", len(await feedback_rows(trace_id)) == 1)

        print("\n=== 3. failure inbox ===")
        inbox = session.get(f"/v1/projects/{project_id}/failure-inbox").json()
        check("the trace is in the inbox", inbox["total"] == 1, f"total {inbox['total']}")
        check("carrying the comment",
              bool(inbox["items"] and inbox["items"][0]["feedback"][0]["comment"] == COMMENT))
        check("and the trace's own name",
              bool(inbox["items"] and inbox["items"][0]["trace"]["name"] == "ask"))

        print("\n=== 4. list filter and trace detail ===")
        negative = session.get(
            f"/v1/projects/{project_id}/traces?feedback=negative"
        ).json()["items"]
        check("?feedback=negative finds it", [t["id"] for t in negative] == [trace_id],
              f"{len(negative)} traces")
        check("its badge counts are right",
              bool(negative and negative[0]["feedback"] == {"negative": 1, "positive": 0}),
              str(negative[0]["feedback"]) if negative else "")
        unrated = session.get(
            f"/v1/projects/{project_id}/traces?feedback=none"
        ).json()["items"]
        check("?feedback=none excludes it", unrated == [], f"{len(unrated)} traces")

        detail = session.get(f"/v1/traces/{trace_id}").json()
        check("the trace header shows what the user said",
              [e["comment"] for e in detail["feedback_events"]] == [COMMENT])

        print("\n=== 5. the same user changes their mind ===")
        submit_feedback(api_key, trace_id, 1, comment="actually fine", user_ref=USER)
        await feedback.process_stream(await feedback.read_batch())
        rows = await feedback_rows(trace_id)
        check("still one row, not two contradictory ones", len(rows) == 1, f"{len(rows)} rows")
        check("flipped to a thumbs-up", bool(rows and rows[0]["rating"] == 1),
              f"rating {rows[0]['rating']}" if rows else "")
        cleared = session.get(f"/v1/projects/{project_id}/failure-inbox").json()
        check("and it leaves the failure inbox", cleared["total"] == 0, f"total {cleared['total']}")

        # Back to a thumbs-down for the rest of the run.
        submit_feedback(api_key, trace_id, -1, comment=COMMENT, user_ref=USER)
        await feedback.process_stream(await feedback.read_batch())
        check("changing back is still one row", len(await feedback_rows(trace_id)) == 1)

        print("\n=== 6. another tenant cannot attach feedback ===")
        other_session, other_project, other_key = new_tenant("other")
        stranger = submit_feedback(other_key, trace_id, -1, comment="not mine")
        # 202 on purpose: the endpoint does not know yet, and telling a caller
        # whose trace an id is would be an enumeration oracle.
        check("their submission is accepted at the edge", stranger.status_code == 202,
              str(stranger.status_code))
        await feedback.process_stream(await feedback.read_batch())
        check("but never written", len(await feedback_rows(trace_id)) == 1,
              f"{len(await feedback_rows(trace_id))} rows")
        check("and not retried either", await redis.zcard(FEEDBACK_DEFERRED_KEY) == 0)
        check("their inbox stays empty",
              other_session.get(f"/v1/projects/{other_project}/failure-inbox").json()["total"] == 0)
        other_session.close()

        print("\n=== 7. promote the failure into a dataset ===")
        dataset = session.post(
            f"/v1/projects/{project_id}/datasets",
            json={"name": f"failures-{uuid.uuid4().hex[:6]}"},
        ).json()
        promoted = session.post(
            f"/v1/datasets/{dataset['id']}/promote", json={"trace_ids": [trace_id]}
        ).json()
        check("the trace was promoted", promoted["promoted"] == 1, str(promoted))
        drained = session.get(f"/v1/projects/{project_id}/failure-inbox").json()
        check("promoting is what clears the inbox", drained["total"] == 0,
              f"total {drained['total']}")

        items = session.get(f"/v1/datasets/{dataset['id']}/items").json()["items"]
        check("the item points back at the trace",
              [i["source_trace_id"] for i in items] == [trace_id])
        check("and kept what the app actually said (ADR-0018)",
              bool(items and (items[0]["metadata"] or {}).get("recorded_output") == BAD_ANSWER))

        print("\n=== 8. the next eval run includes it ===")
        run = session.post(
            "/v1/eval-runs",
            json={
                "dataset_id": dataset["id"],
                "name": "post-feedback",
                # `rescore` grades the output the app actually produced, so this
                # run needs no model credentials at all.
                "target": {"mode": "rescore"},
                # Deterministic: does the recorded answer refuse? No credits spent.
                "graders": [{"name": "not_contains", "config": {"value": "I'm sorry"}}],
            },
        )
        check("run created", run.status_code == 201,
              f"{run.status_code} {run.text[:160]}")
        if run.status_code != 201:
            raise SystemExit(1)
        run_id = run.json()["id"]
        await evals.execute(uuid.UUID(run_id))

        results = await result_rows(run_id)
        check("the promoted item was scored", len(results) == 1, f"{len(results)} results")
        scored = (results[0]["scores"] or {}).get("not_contains") if results else None
        check("and it scored the failure as a failure",
              bool(scored and scored.get("score") == 0.0),
              json.dumps(scored) if scored else "no score")

        state = session.get(f"/v1/eval-runs/{run_id}").json()
        check("the run completed", state["status"] == "completed", state["status"])
        summary = state.get("summary") or {}
        check("its summary counts the promoted item", summary.get("total_items") == 1,
              json.dumps(summary)[:200])
        grader_summary = (summary.get("graders") or {}).get("not_contains") or {}
        check("and reports the failure in the mean", grader_summary.get("mean") == 0.0,
              json.dumps(grader_summary)[:200])
    finally:
        session.close()
        await redis.aclose()
        await engine.dispose()
        # After `engine.dispose()` on purpose: this opens its own asyncpg
        # connection, so it does not depend on the ORM engine still being alive.
        if KEEP:
            print(f"\n(kept: projects {', '.join(CREATED_PROJECTS)})")
        elif CREATED_PROJECTS:
            print("\n=== cleaning up the acceptance tenants ===")
            for created in CREATED_PROJECTS:
                await drop_tenant(settings.database_url, created)

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 9 acceptance PASSED")
    return 0


if __name__ == "__main__":
    start = time.perf_counter()
    code = asyncio.run(main())
    print(f"({time.perf_counter() - start:.1f}s)")
    sys.exit(code)
