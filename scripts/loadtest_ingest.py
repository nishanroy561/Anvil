"""Phase-2 acceptance harness (plan/ingestion.md §6).

Pumps N synthetic traces through the real HTTP endpoint while killing and
restarting the worker mid-run, then asserts **zero loss and zero duplicates**.
The kill is the point: at-least-once delivery plus guarded upserts should mean a
dead consumer costs latency, not data.

Prerequisites: Postgres + Redis up, migrations applied, and the API running
(`python -m uvicorn anvil_api.main:app --port 8000` from `apps/api`).

    python scripts/loadtest_ingest.py --traces 1000 --kill-worker

The run provisions a throwaway tenant and **removes it again on the way out**,
pass or fail, so repeated acceptance runs don't silt up the dev database with
`Loadtest <hex>` orgs. Pass `--keep` to leave it behind and inspect it in the
dashboard.

Exit code 0 means the acceptance criterion passed.
"""

import argparse
import asyncio
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from _tenants import drop_tenant

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "apps" / "worker"

MODELS = ["claude-sonnet-5", "gpt-4o", "claude-haiku-4-5", "some-local-model"]
TRACE_NAMES = ["rag query", "support chat", "summarize", "classify ticket"]


def make_trace() -> dict:
    """One synthetic trace with 5–10 spans and a plausible shape."""
    started = datetime.now(timezone.utc) - timedelta(seconds=random.uniform(0, 3600))
    span_count = random.randint(5, 10)
    root_id = str(uuid.uuid4())
    duration_ms = random.randint(200, 4000)

    spans = [
        {
            "id": root_id,
            "name": "pipeline",
            "type": "chain",
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(milliseconds=duration_ms)).isoformat(),
        }
    ]
    for index in range(span_count - 1):
        offset = timedelta(milliseconds=random.randint(0, duration_ms // 2))
        spans.append(
            {
                "id": str(uuid.uuid4()),
                "parent_span_id": root_id,
                "name": f"step {index}",
                "type": random.choice(["llm", "retrieval", "tool"]),
                "started_at": (started + offset).isoformat(),
                "ended_at": (started + offset + timedelta(milliseconds=120)).isoformat(),
                "tokens_in": random.randint(50, 2000),
                "tokens_out": random.randint(10, 800),
            }
        )

    return {
        "id": str(uuid.uuid4()),
        "name": random.choice(TRACE_NAMES),
        "status": "error" if random.random() < 0.08 else "ok",
        "model": random.choice(MODELS),
        "input": {"question": "how do i reset my password"},
        "output": {"answer": "use the reset link in settings"},
        "metadata": {"env": "loadtest", "tier": random.choice(["free", "pro"])},
        "session_id": str(uuid.uuid4()),
        "started_at": started.isoformat(),
        "ended_at": (started + timedelta(milliseconds=duration_ms)).isoformat(),
        "spans": spans,
    }


PASSWORD = "loadtest-password"


async def provision_tenant(base_url: str) -> tuple[str, str, str]:
    """Fresh org/project/key over HTTP, so the run never touches demo data."""
    suffix = uuid.uuid4().hex[:8]
    email = f"loadtest-{suffix}@example.com"
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        signup = await client.post(
            "/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "org_name": f"Loadtest {suffix}"},
        )
        signup.raise_for_status()
        project = (await client.get("/v1/projects")).json()[0]
        key = await client.post(
            f"/v1/projects/{project['id']}/api-keys", json={"name": "loadtest"}
        )
        key.raise_for_status()
        return project["id"], key.json()["key"], email


async def verify_reads(base_url: str, email: str, project_id: str, expected: int) -> list[str]:
    """The other half of the acceptance criterion: list/detail/metrics must
    return correct data over the ingested set (plan/ingestion.md §6)."""
    problems = []
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        login = await client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        login.raise_for_status()

        # Walk every page and confirm the cursor visits each trace exactly once.
        seen, cursor, pages = [], None, 0
        while True:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = (
                await client.get(f"/v1/projects/{project_id}/traces", params=params)
            ).json()
            seen.extend(t["id"] for t in page["items"])
            pages += 1
            cursor = page["next_cursor"]
            if not cursor:
                break

        if len(seen) != expected:
            problems.append(f"list returned {len(seen)} traces, expected {expected}")
        if len(set(seen)) != len(seen):
            problems.append(f"list repeated {len(seen) - len(set(seen))} traces across pages")

        detail = (await client.get(f"/v1/traces/{seen[0]}")).json()
        if not detail.get("spans"):
            problems.append("detail returned no spans")
        if any(s["trace_id"] != seen[0] for s in detail.get("spans", [])):
            problems.append("detail returned spans from another trace")

        overview = (
            await client.get(f"/v1/projects/{project_id}/metrics/overview")
        ).json()
        if overview["total_traces"] != expected:
            problems.append(
                f"overview counted {overview['total_traces']} traces, expected {expected}"
            )
        if overview["p95_latency_ms"] is None:
            problems.append("overview reported no p95 latency")
        if overview["total_cost_usd"] <= 0:
            problems.append("overview reported no cost; pricing may not have resolved")

        search = (
            await client.get(
                f"/v1/projects/{project_id}/traces", params={"q": "password", "limit": 5}
            )
        ).json()
        if not search["items"]:
            problems.append("free-text search matched nothing on known-matching input")

        print(f"  list: {len(seen)} traces over {pages} pages")
        print(f"  overview: {overview['total_traces']} traces, "
              f"p95 {overview['p95_latency_ms']}ms, ${overview['total_cost_usd']:.4f}")
        print(f"  detail: {len(detail.get('spans', []))} spans on first trace")
    return problems


def start_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "anvil_worker.main"],
        cwd=WORKER_DIR,
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def persisted_counts(database_url: str, project_id: str) -> tuple[int, int, int]:
    """(distinct traces, total trace rows, spans). The first two differing would
    mean duplicates — impossible through a primary key, but asserted anyway
    because that is the claim being tested."""
    import asyncpg

    dsn = database_url.replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dsn)
    try:
        distinct = await conn.fetchval(
            "SELECT count(DISTINCT id) FROM traces WHERE project_id = $1",
            uuid.UUID(project_id),
        )
        total = await conn.fetchval(
            "SELECT count(*) FROM traces WHERE project_id = $1", uuid.UUID(project_id)
        )
        spans = await conn.fetchval(
            "SELECT count(*) FROM spans WHERE project_id = $1", uuid.UUID(project_id)
        )
        return distinct, total, spans
    finally:
        await conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--kill-worker",
        action="store_true",
        help="Kill and restart the worker mid-run (the actual acceptance test)",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the seeded tenant behind (to inspect it in the dashboard)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://anvil:anvil@localhost:5432/anvil"
        ),
    )
    args = parser.parse_args()

    print(f"-> provisioning tenant on {args.api}")
    project_id, api_key, email = await provision_tenant(args.api)
    print(f"  project {project_id}")

    worker = start_worker()
    print(f"-> worker started (pid {worker.pid})")
    killed_at = None

    sent = 0
    expected_spans = 0
    kill_point = args.traces // 3
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(
            base_url=args.api,
            timeout=60,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as client:
            while sent < args.traces:
                size = min(args.batch_size, args.traces - sent)
                batch = [make_trace() for _ in range(size)]
                expected_spans += sum(len(t["spans"]) for t in batch)

                response = await client.post("/v1/ingest/traces", json={"traces": batch})
                if response.status_code == 429:
                    # Backpressure or rate limit: back off and retry, exactly as
                    # an SDK would. Not a failure.
                    expected_spans -= sum(len(t["spans"]) for t in batch)
                    await asyncio.sleep(float(response.headers.get("Retry-After", 1)))
                    continue
                response.raise_for_status()
                sent += size

                if args.kill_worker and killed_at is None and sent >= kill_point:
                    worker.kill()
                    worker.wait(timeout=10)
                    killed_at = sent
                    print(f"-> worker KILLED after {sent} traces sent")
                    worker = start_worker()
                    print(f"-> worker restarted (pid {worker.pid})")

        print(f"-> {sent} traces accepted in {time.monotonic() - started:.1f}s; draining")

        deadline = time.monotonic() + args.timeout
        distinct = total = spans = 0
        while time.monotonic() < deadline:
            distinct, total, spans = await persisted_counts(args.database_url, project_id)
            if distinct >= args.traces:
                break
            await asyncio.sleep(1)

        elapsed = time.monotonic() - started
    finally:
        worker.kill()

    try:
        print()
        print(f"  sent:             {sent}")
        print(f"  distinct traces:  {distinct}")
        print(f"  trace rows:       {total}")
        print(f"  spans:            {spans} (expected {expected_spans})")
        print(f"  worker killed at: {killed_at if killed_at else 'not killed'}")
        print(f"  elapsed:          {elapsed:.1f}s")
        print()

        failures = []
        if distinct != args.traces:
            failures.append(f"LOSS: {args.traces - distinct} traces never persisted")
        if total != distinct:
            failures.append(f"DUPLICATES: {total} rows for {distinct} distinct ids")
        if spans != expected_spans:
            failures.append(f"SPAN MISMATCH: {spans} persisted, {expected_spans} expected")

        if not failures:
            print("-> verifying read APIs")
            failures.extend(await verify_reads(args.api, email, project_id, args.traces))
            print()

        if failures:
            for failure in failures:
                print(f"FAIL  {failure}")
            # The tenant is about to be removed, which is unhelpful when you
            # wanted to go and look at what went wrong. Say so while the failure
            # is on screen rather than in the docstring.
            if not args.keep:
                print("\n(re-run with --keep to leave the data behind for inspection)")
            return 1

        print("PASS  zero loss, zero duplicates across a mid-run worker kill")
        return 0
    finally:
        if args.keep:
            print(f"\n(kept: project {project_id})")
        else:
            print("\n-> cleaning up the seeded tenant")
            await drop_tenant(args.database_url, project_id)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
