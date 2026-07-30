"""Phase 10 acceptance: a 1-day window expires traces in batches, and the
datasets built from them still evaluate.

    docker compose up -d postgres redis
    cd apps/api && python -m uvicorn anvil_api.main:app --port 8000
    python scripts/retention_acceptance.py --force        # exits 0 on pass

**This script deletes data, and not only its own.** The retention window is a
single global setting (ADR-0019 left no per-org windows), so a 1-day sweep expires
every trace in the database older than a day — including whatever a dev box has
accumulated. That is the behaviour under test, so it cannot be scoped away; hence
`--force`. Without it the script reports what it would delete and stops.

API live over real HTTP; the retention pass and the eval runner are driven
in-process, same rationale as Phases 8 and 9 — the daily loop's cadence is not
the thing under test, and waiting 24 hours to observe one sweep would make the
acceptance useless rather than stronger.

The order matters. The dataset is promoted **before** the sweep, so the traces it
was built from are deleted underneath it, which is the case checklist row 24
exists for:

  1. unset window          -> nothing is deleted, however old the traces are
  2. a 0-day window        -> refused, not interpreted as "delete everything"
  3. a 1-day window        -> expired traces go, fresh traffic stays
  4. batching              -> more statements than one, none over the batch size
  5. datasets survive      -> source_trace_id null, payloads intact, eval completes
  6. usage rollups         -> retention does not rewrite the month as zero

No provider credits are spent: the grader is the deterministic `contains`.
"""

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "worker"))

import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import func, insert, select, text  # noqa: E402

from anvil_worker import retention as ret  # noqa: E402
from anvil_worker.config import get_settings  # noqa: E402
from anvil_worker.db import engine  # noqa: E402
from anvil_worker.eval_runner import EvalRunner  # noqa: E402
from anvil_worker.graders import load_graders  # noqa: E402
from anvil_worker.tables import eval_results, org_usage, traces  # noqa: E402

BASE = "http://localhost:8000"
PASSWORD = "hunter2hunter2"
ANSWER = "Refunds land on your original payment method in 5-7 business days."


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_tenant(label: str) -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=BASE, timeout=30.0)
    email = f"phase10-{label}-{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "org_name": f"Phase 10 {label}"},
    ).raise_for_status()
    return client, client.get("/v1/projects").json()[0]["id"]


async def seed_traces(project_id: str, n: int, days_ago: float) -> list[uuid.UUID]:
    """Written straight to Postgres: this is about expiry, and routing through
    ingest would only add a worker dependency and a wait."""
    ids = [uuid.uuid4() for _ in range(n)]
    started = now() - timedelta(days=days_ago)
    async with engine.begin() as conn:
        await conn.execute(
            insert(traces),
            [
                {
                    "id": tid,
                    "project_id": uuid.UUID(project_id),
                    "name": "ask",
                    "status": "ok",
                    "input": {"question": "how long do refunds take?"},
                    "output": ANSWER,
                    "started_at": started,
                    "ended_at": started + timedelta(milliseconds=380),
                    "total_cost_usd": 0.00062,
                }
                for tid in ids
            ],
        )
    return ids


async def count_traces(project_id: str) -> int:
    async with engine.begin() as conn:
        return await conn.scalar(
            select(func.count()).select_from(traces).where(
                traces.c.project_id == uuid.UUID(project_id)
            )
        )


async def usage_row(project_id: str) -> dict | None:
    async with engine.begin() as conn:
        org_id = await conn.scalar(
            text("SELECT org_id FROM projects WHERE id = :id"),
            {"id": uuid.UUID(project_id)},
        )
        row = (
            await conn.execute(select(org_usage).where(org_usage.c.org_id == org_id))
        ).mappings().first()
    return dict(row) if row else None


async def main() -> int:
    load_graders()
    settings = get_settings()
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label} {detail}")

    try:
        with httpx.Client(base_url=BASE, timeout=5.0) as probe:
            health = probe.get("/healthz").json()
        if not health.get("db"):
            print(f"API is up but unhealthy: {health}")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"cannot reach the API at {BASE}: {exc}")
        print("start it with: cd apps/api && python -m uvicorn anvil_api.main:app --port 8000")
        return 1

    # What a 1-day sweep would take out beyond this script's own traffic. Printed
    # before anything is deleted, because "it also removed my demo data" is a
    # surprise worth spending four lines to avoid.
    async with engine.begin() as conn:
        at_risk = await conn.scalar(
            select(func.count()).select_from(traces).where(
                traces.c.started_at < now() - timedelta(days=1)
            )
        )
    if "--force" not in sys.argv:
        print(
            f"This acceptance runs a global 1-day retention sweep.\n"
            f"{at_risk} trace(s) already in this database are older than a day and "
            f"would be deleted.\n"
            f"Re-run with --force if that is a disposable dev database."
        )
        return 2
    if at_risk:
        print(f"note: {at_risk} pre-existing trace(s) older than a day will also go")

    session, project_id = new_tenant("main")
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    evals = EvalRunner(redis)

    # Force deterministic, small batches so the batching check is meaningful.
    original_batch = settings.retention_batch_size
    original_sleep = settings.retention_batch_sleep_s
    object.__setattr__(settings, "retention_batch_size", 4)
    object.__setattr__(settings, "retention_batch_sleep_s", 0.0)

    try:
        print("\n=== setup ===")
        expired = await seed_traces(project_id, 14, days_ago=9)
        fresh = await seed_traces(project_id, 5, days_ago=0.2)
        check("seeded traffic", await count_traces(project_id) == 19,
              f"{await count_traces(project_id)} traces")

        print("\n=== 1. no window configured ===")
        object.__setattr__(settings, "retention_days", None)
        deleted = await ret.sweep()
        check("nothing is deleted", deleted == 0, f"deleted {deleted}")
        check("every trace is still there", await count_traces(project_id) == 19)

        print("\n=== 2. a zero-day window is refused ===")
        object.__setattr__(settings, "retention_days", 0)
        deleted = await ret.sweep()
        check("refused rather than interpreted", deleted == 0, f"deleted {deleted}")
        check("still 19 traces", await count_traces(project_id) == 19)

        print("\n=== 3. promote a dataset BEFORE the sweep ===")
        dataset = session.post(
            f"/v1/projects/{project_id}/datasets",
            json={"name": f"golden-{uuid.uuid4().hex[:6]}"},
        ).json()
        promoted = session.post(
            f"/v1/datasets/{dataset['id']}/promote",
            json={"trace_ids": [str(t) for t in expired[:6]]},
        ).json()
        check("six expiring traces promoted", promoted["promoted"] == 6, json.dumps(promoted))

        print("\n=== 4. a one-day window ===")
        object.__setattr__(settings, "retention_days", 1)
        batches: list[int] = []
        original_delete = ret.delete_batch

        async def counting(before, limit):
            n = await original_delete(before, limit)
            batches.append(n)
            return n

        ret.delete_batch = counting  # type: ignore[assignment]
        try:
            deleted = await ret.sweep()
        finally:
            ret.delete_batch = original_delete  # type: ignore[assignment]

        # `deleted` counts the whole database, not this tenant: the window is
        # global, so any other old traffic on the box goes too. The meaningful
        # assertion is about *these* traces.
        async with engine.begin() as conn:
            left = await conn.scalar(
                select(func.count()).select_from(traces).where(traces.c.id.in_(list(expired)))
            )
        check("this tenant's expired traces are gone", left == 0, f"{left} still present")
        check("the sweep reported at least them", deleted >= 14, f"deleted {deleted} db-wide")
        check("fresh traffic is untouched", await count_traces(project_id) == 5,
              f"{await count_traces(project_id)} left")
        async with engine.begin() as conn:
            survivors = await conn.scalar(
                select(func.count()).select_from(traces).where(traces.c.id.in_(list(fresh)))
            )
        check("and it is the *fresh* traces that survived", survivors == 5,
              f"{survivors} of 5")

        print("\n=== 5. it went in batches ===")
        check("more than one statement", len([b for b in batches if b]) > 1,
              f"batches {batches}")
        check("no statement exceeded the batch size", max(batches) <= 4,
              f"largest {max(batches)}")
        check("the last pass found nothing, which is what ended the loop",
              batches[-1] == 0, f"batches {batches}")

        print("\n=== 6. the dataset outlived its traces ===")
        items = session.get(f"/v1/datasets/{dataset['id']}/items").json()
        check("all six items are still there", items["total"] == 6, json.dumps(items)[:120])
        links = [i["source_trace_id"] for i in items["items"]]
        check("every back-link went null, not the row", all(l is None for l in links),
              f"links {links}")
        first = items["items"][0]
        check("the input survived", bool((first.get("input") or {}).get("question")),
              json.dumps(first.get("input"))[:80])
        check("and so did the recorded output (ADR-0018)",
              (first.get("metadata") or {}).get("recorded_output") == ANSWER,
              json.dumps((first.get("metadata") or {}).get("recorded_output"))[:80])

        print("\n=== 7. and it still evaluates ===")
        run = session.post(
            "/v1/eval-runs",
            json={
                "dataset_id": dataset["id"],
                "name": "post-retention",
                "target": {"mode": "rescore"},
                "graders": [{"name": "contains", "config": {"value": "business days"}}],
            },
        )
        check("run created", run.status_code == 201, f"{run.status_code} {run.text[:140]}")
        if run.status_code != 201:
            raise SystemExit(1)
        run_id = run.json()["id"]
        await evals.execute(uuid.UUID(run_id))

        async with engine.begin() as conn:
            scored = (
                await conn.execute(
                    select(eval_results).where(eval_results.c.run_id == uuid.UUID(run_id))
                )
            ).mappings().all()
        check("every orphaned item was graded", len(scored) == 6, f"{len(scored)} results")
        means = [
            (r["scores"] or {}).get("contains", {}).get("score") for r in scored
        ]
        check("and graded from its own copy of the data, not the trace",
              all(m == 1.0 for m in means), f"scores {means}")
        state = session.get(f"/v1/eval-runs/{run_id}").json()
        check("the run completed", state["status"] == "completed", state["status"])

        print("\n=== 8. retention did not rewrite the usage rollup ===")
        # Recompute *after* the sweep: the traces are gone, so a naive recount
        # would report zero for the month they were ingested in.
        await ret.recompute_usage()
        before = await usage_row(project_id)
        check("a rollup exists", before is not None)
        first_count = before["traces_ingested"] if before else 0
        check("it counts the surviving traffic at least", first_count >= 5,
              f"traces_ingested {first_count}")

        # A second pass must never revise it down (ADR-0022).
        await ret.recompute_usage()
        after = await usage_row(project_id)
        check("a second pass does not reduce it",
              after is not None and after["traces_ingested"] >= first_count,
              f"{first_count} -> {after['traces_ingested'] if after else None}")
        check("storage_bytes is left null",
              after is not None and after["storage_bytes"] is None)
    finally:
        object.__setattr__(settings, "retention_batch_size", original_batch)
        object.__setattr__(settings, "retention_batch_sleep_s", original_sleep)
        object.__setattr__(settings, "retention_days", None)
        session.close()
        await redis.aclose()
        await engine.dispose()

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 10 acceptance PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
