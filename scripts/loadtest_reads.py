"""Phase 11 load test: read latency at 50k traces (plan.md Phase 11 §3).

    docker compose up -d --wait
    docker compose --profile observability up -d          # optional but see below
    python scripts/loadtest_reads.py --traces 50000

Every dashboard read must stay under 500 ms server-side at this volume, and
free-text search must reach it **through the trigram index** rather than by
scanning the table. The second half is the point: a seq scan over 50k rows is
fast enough to pass a stopwatch and catastrophic at 5 million, so the plan asks
for `EXPLAIN` evidence and not just a timing.

Complement to `loadtest_ingest.py`, which pumps traces through the real HTTP
endpoint and kills the worker mid-run to prove zero loss. That covers the *write*
path. Rows here are bulk-inserted straight into Postgres — 50k traces through
ingest would take an hour and prove nothing this script is asking about — with
`search_text` built exactly as `derive.build_search_text` builds it, since that
column is the whole subject of the index check.

`ANALYZE` runs before any measurement. Without statistics the planner guesses,
and an `EXPLAIN` read off an unanalyzed table is a coin toss dressed as evidence.
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx

MODELS = ["claude-sonnet-5", "gpt-4o", "claude-haiku-4-5", "gemini-2.5-flash", "some-local-model"]
NAMES = ["rag query", "support chat", "summarize", "classify ticket", "ask"]
STATUSES = ["ok"] * 92 + ["error"] * 6 + ["abandoned"] * 2

# Phrases the search test looks for. Rare on purpose: a term matching 40% of rows
# is a test of the heap fetch, not of the index.
NEEDLE = "zarquon protocol"
QUESTIONS = [
    "how long do refunds take",
    "where is my order",
    "can I change my shipping address",
    "why was my card declined",
    "how do I rotate an api key",
]
ANSWERS = [
    "refunds land on the original payment method in 5-7 business days",
    "your order shipped on tuesday and is out for delivery",
    "shipping addresses can be changed until the label is printed",
    "the issuer declined it; try another card or contact your bank",
    "rotate keys from the project settings page",
]

BUDGET_MS = 500.0


def dsn_from(url: str) -> str:
    """SQLAlchemy URL -> asyncpg DSN."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def search_text_of(name: str, question: str, answer: str) -> str:
    """Mirrors `anvil_worker.derive.build_search_text`: name + root input/output,
    lowercased. Span payloads are deliberately excluded (ADR-0004/0005)."""
    return f"{name} {question} {answer}".lower()[:8000]


async def provision(base_url: str) -> tuple[httpx.AsyncClient, str, str]:
    """Its own tenant, so the numbers are not perturbed by whatever else is in the
    database — and so the script never reads another tenant's rows."""
    client = httpx.AsyncClient(base_url=base_url, timeout=120.0)
    email = f"loadtest-{uuid.uuid4().hex[:8]}@example.com"
    res = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "hunter2hunter2", "org_name": "Read Load Test"},
    )
    res.raise_for_status()
    project_id = (await client.get("/v1/projects")).json()[0]["id"]
    key = (
        await client.post(f"/v1/projects/{project_id}/api-keys", json={"name": "loadtest"})
    ).json()["key"]
    return client, project_id, key


async def seed(conn: asyncpg.Connection, project_id: str, total: int, spans_each: int) -> str:
    """Bulk-insert traces and spans. Returns the id of the 100-span trace."""
    pid = uuid.UUID(project_id)
    now = datetime.now(timezone.utc)
    chunk = 2000

    written = 0
    while written < total:
        batch = min(chunk, total - written)
        trace_rows, span_rows = [], []

        for i in range(batch):
            tid = uuid.uuid4()
            name = random.choice(NAMES)
            question = random.choice(QUESTIONS)
            answer = random.choice(ANSWERS)
            # A handful of rows carry the rare needle, so the search test measures
            # a selective query rather than a full-table match.
            if random.random() < 0.001:
                answer = f"{answer} {NEEDLE}"
            # Spread over 30 days: the keyset index is (project_id, started_at),
            # and every row sharing a timestamp would make paging meaningless.
            started = now - timedelta(seconds=random.uniform(0, 30 * 86400))
            latency = random.randint(120, 6000)
            trace_rows.append(
                (
                    tid, pid, name, random.choice(STATUSES),
                    json.dumps({"question": question}), json.dumps(answer),
                    json.dumps({"env": "prod", "channel": random.choice(["web", "api", "mobile"])}),
                    search_text_of(name, question, answer),
                    random.choice(MODELS),
                    random.randint(50, 4000), random.randint(20, 900),
                    round(random.uniform(0.0001, 0.05), 6),
                    latency, f"sess-{random.randint(1, 2000):04d}",
                    f"user-{random.randint(1, 5000):04d}",
                    started, started + timedelta(milliseconds=latency),
                )
            )
            for s in range(spans_each):
                span_rows.append(
                    (
                        uuid.uuid4(), tid, None, pid, f"step-{s}",
                        random.choice(["llm", "retrieval", "tool", "chain"]),
                        json.dumps({"in": question}), json.dumps({"out": answer}),
                        json.dumps({"model": random.choice(MODELS)}),
                        random.randint(10, 800), random.randint(5, 300),
                        round(random.uniform(0.0001, 0.01), 6),
                        "ok", None,
                        started, started + timedelta(milliseconds=latency // max(spans_each, 1)),
                    )
                )

        await conn.copy_records_to_table(
            "traces",
            records=trace_rows,
            columns=[
                "id", "project_id", "name", "status", "input", "output", "metadata",
                "search_text", "model", "total_tokens_in", "total_tokens_out",
                "total_cost_usd", "latency_ms", "session_id", "user_ref",
                "started_at", "ended_at",
            ],
        )
        await conn.copy_records_to_table(
            "spans",
            records=span_rows,
            columns=[
                "id", "trace_id", "parent_span_id", "project_id", "name", "type",
                "input", "output", "attributes", "tokens_in", "tokens_out",
                "cost_usd", "status", "error", "started_at", "ended_at",
            ],
        )
        written += batch
        print(f"  seeded {written}/{total}", end="\r", flush=True)

    # One deliberately deep trace: the waterfall budget is about span count, and
    # every other row here has a handful.
    deep_id = uuid.uuid4()
    started = now - timedelta(minutes=5)
    await conn.execute(
        "INSERT INTO traces (id, project_id, name, status, input, output, metadata, "
        "search_text, model, latency_ms, started_at, ended_at) VALUES "
        "($1,$2,'deep_waterfall','ok',$3,$4,$5,$6,'claude-sonnet-5',11000,$7,$8)",
        deep_id, pid, json.dumps({"question": "deep trace"}), json.dumps("done"),
        json.dumps({"synthetic": True}), "deep_waterfall deep trace done",
        started, started + timedelta(seconds=11),
    )
    deep_spans = []
    for s in range(103):
        offset = timedelta(milliseconds=s * 100)
        deep_spans.append(
            (
                uuid.uuid4(), deep_id, None, pid, f"stage_{s}",
                ["llm", "retrieval", "tool", "chain", "embedding"][s % 5],
                json.dumps({"i": s}), json.dumps({"o": s}), json.dumps({"depth": s}),
                10, 5, 0.0001, "ok", None,
                started + offset, started + offset + timedelta(milliseconds=90),
            )
        )
    await conn.copy_records_to_table(
        "spans",
        records=deep_spans,
        columns=[
            "id", "trace_id", "parent_span_id", "project_id", "name", "type",
            "input", "output", "attributes", "tokens_in", "tokens_out", "cost_usd",
            "status", "error", "started_at", "ended_at",
        ],
    )
    print(f"  seeded {written}/{total} + a 103-span trace")
    return str(deep_id)


async def timed(client: httpx.AsyncClient, path: str, runs: int = 5) -> tuple[float, int]:
    """Median wall time over `runs`, plus the status of the last call.

    Median rather than mean: one cold cache should not decide the verdict, and
    one lucky run should not either. Loopback HTTP adds well under a millisecond
    against a 500 ms budget.
    """
    samples, status = [], 0
    for _ in range(runs):
        start = time.perf_counter()
        res = await client.get(path)
        samples.append((time.perf_counter() - start) * 1000)
        status = res.status_code
    return statistics.median(samples), status


async def explain_search(conn: asyncpg.Connection, project_id: str) -> str:
    """The plan for the free-text query the read API issues."""
    rows = await conn.fetch(
        "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM traces "
        "WHERE project_id = $1 AND started_at >= now() - interval '7 days' "
        "AND search_text ILIKE $2 "
        "ORDER BY started_at DESC, id DESC LIMIT 51",
        uuid.UUID(project_id), f"%{NEEDLE}%",
    )
    return "\n".join(r["QUERY PLAN"] for r in rows)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", type=int, default=50_000)
    parser.add_argument("--spans-each", type=int, default=2)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--database-url",
        default="postgresql+asyncpg://anvil:anvil@localhost:5432/anvil",
    )
    parser.add_argument("--keep", action="store_true", help="leave the seeded tenant behind")
    args = parser.parse_args()

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label} {detail}")

    try:
        async with httpx.AsyncClient(timeout=5.0) as probe:
            health = (await probe.get(f"{args.api}/healthz")).json()
        if not health.get("db"):
            print(f"API unhealthy: {health}")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"cannot reach the API at {args.api}: {exc}")
        return 1

    client, project_id, _key = await provision(args.api)
    conn = await asyncpg.connect(dsn_from(args.database_url))

    try:
        print(f"\n=== seeding {args.traces} traces ===")
        t0 = time.perf_counter()
        deep_id = await seed(conn, project_id, args.traces, args.spans_each)
        print(f"  took {time.perf_counter() - t0:.1f}s")

        # Before any measurement. An EXPLAIN off an unanalyzed table is a guess.
        print("\n=== ANALYZE ===")
        t0 = time.perf_counter()
        await conn.execute("ANALYZE traces, spans")
        print(f"  took {time.perf_counter() - t0:.1f}s")

        count = await conn.fetchval(
            "SELECT count(*) FROM traces WHERE project_id = $1", uuid.UUID(project_id)
        )
        check("rows are in place", count >= args.traces, f"{count} traces")

        print(f"\n=== read latency (budget {BUDGET_MS:.0f} ms, median of 5) ===")
        cases = [
            ("traces list, first page", f"/v1/projects/{project_id}/traces?limit=50"),
            ("traces list, filtered by status", f"/v1/projects/{project_id}/traces?status=error&limit=50"),
            ("traces list, filtered by model", f"/v1/projects/{project_id}/traces?model=gpt-4o&limit=50"),
            ("metrics overview, 30d by day", f"/v1/projects/{project_id}/metrics/overview?interval=day"),
            ("free-text search", f"/v1/projects/{project_id}/traces?q={NEEDLE.replace(' ', '+')}"),
            ("103-span waterfall", f"/v1/traces/{deep_id}"),
        ]
        for label, path in cases:
            ms, status = await timed(client, path)
            check(f"{label} < {BUDGET_MS:.0f} ms", status == 200 and ms < BUDGET_MS,
                  f"{ms:.0f} ms (HTTP {status})")

        # Keyset paging: page 50 must cost what page 1 costs, which is the whole
        # reason the list is not OFFSET-based.
        print("\n=== keyset paging holds its cost ===")
        cursor, first_ms, last_ms = None, None, None
        for page in range(12):
            path = f"/v1/projects/{project_id}/traces?limit=50" + (f"&cursor={cursor}" if cursor else "")
            start = time.perf_counter()
            body = (await client.get(path)).json()
            elapsed = (time.perf_counter() - start) * 1000
            if page == 0:
                first_ms = elapsed
            last_ms = elapsed
            cursor = body.get("next_cursor")
            if not cursor:
                break
        check("page 12 is not slower than page 1", last_ms < max(first_ms * 3, BUDGET_MS),
              f"page1 {first_ms:.0f} ms -> page12 {last_ms:.0f} ms")

        print("\n=== search uses the trigram index, not a seq scan ===")
        plan = await explain_search(conn, project_id)
        used_index = "ix_traces_search_trgm" in plan
        seq_scan = "Seq Scan on traces" in plan
        check("plan references ix_traces_search_trgm", used_index)
        check("plan contains no sequential scan of traces", not seq_scan)
        if not (used_index and not seq_scan):
            print("\n--- plan ---")
            print(plan)
        else:
            first_line = plan.splitlines()[0] if plan else ""
            print(f"  plan root: {first_line.strip()}")

        print("\n=== table sizes ===")
        sizes = await conn.fetch(
            "SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' "
            "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 5"
        )
        for row in sizes:
            print(f"  {row['relname']:16} {row['size']}")
    finally:
        if not args.keep:
            print("\n=== cleaning up the seeded tenant ===")
            await conn.execute(
                "DELETE FROM traces WHERE project_id = $1", uuid.UUID(project_id)
            )
            org_id = await conn.fetchval(
                "SELECT org_id FROM projects WHERE id = $1", uuid.UUID(project_id)
            )
            await conn.execute("DELETE FROM api_keys WHERE project_id = $1", uuid.UUID(project_id))
            await conn.execute("DELETE FROM projects WHERE id = $1", uuid.UUID(project_id))
            await conn.execute("DELETE FROM users WHERE org_id = $1", org_id)
            await conn.execute("DELETE FROM org_usage WHERE org_id = $1", org_id)
            await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        else:
            print(f"\n(kept: project {project_id})")
        await conn.close()
        await client.aclose()

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 11 read load test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
