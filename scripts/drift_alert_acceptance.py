"""Phase 8 acceptance: drift is sampled, scored, alerted, and delivered.

    docker compose up -d postgres redis
    cd apps/api && python -m uvicorn anvil_api.main:app --port 8000
    python scripts/drift_alert_acceptance.py        # exits 0 on pass

Runs the sampler and alerter **in-process** rather than against a live worker,
so the cycle boundaries are deterministic — the criterion is "fires within one
cycle", and waiting 60 s to observe that makes a flaky test, not a stronger one.
The code paths exercised are the ones `anvil_worker.main` schedules.

A local HTTP server stands in for the customer's webhook receiver, so delivery
is proved by receipt rather than by a log line.

  1. healthy traffic  -> monitor scores high, no alert
  2. drifted traffic  -> monitor scores low, alert fires, webhook receives it
  3. the same cycle again -> no duplicate event while it is still open
  4. recovery         -> event resolves, webhook receives the resolution
"""

import asyncio
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "worker"))

from sqlalchemy import insert, select, text  # noqa: E402

from anvil_worker.alerter import evaluate  # noqa: E402
from anvil_worker.channels import load_channels  # noqa: E402
from anvil_worker.db import engine  # noqa: E402
from anvil_worker.graders import load_graders  # noqa: E402
from anvil_worker.sampler import run_monitor  # noqa: E402
from anvil_worker.tables import (  # noqa: E402
    alert_events,
    alert_rules,
    monitor_configs,
    monitor_scores,
    traces,
)

RECEIVED: list[dict] = []

GOOD = "Standard shipping is 3-5 business days. Express orders arrive next day."
BAD = "I'm sorry, I don't have that information available right now."


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        RECEIVED.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass  # keep the acceptance output readable


def now():
    return datetime.now(timezone.utc)


async def setup(webhook_url: str):
    """A fresh project with a quality monitor and an eval_score alert rule."""
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    monitor_id, rule_id = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO organizations (id, name, plan) VALUES (:id, 'Drift Acceptance', 'free')"),
            {"id": org_id},
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, org_id, name, slug, alert_webhook_url) "
                "VALUES (:id, :org, 'Drift', :slug, :url)"
            ),
            {"id": project_id, "org": org_id, "slug": f"drift-{uuid.uuid4().hex[:8]}", "url": webhook_url},
        )
        await conn.execute(
            insert(monitor_configs).values(
                id=monitor_id,
                project_id=project_id,
                name="refusal rate",
                sample_rate=1.0,
                # "did the assistant refuse?" — scores 1 for a real answer,
                # 0 for a refusal. Deterministic, so no judge credits.
                grader_config={"graders": [{"name": "not_contains", "config": {"value": "I'm sorry"}}]},
                schedule_cron="*/10 * * * *",
                enabled=True,
                created_at=now(),
            )
        )
        await conn.execute(
            insert(alert_rules).values(
                id=rule_id,
                project_id=project_id,
                name="quality dropped",
                metric="eval_score",
                comparison="lt",
                threshold=0.5,
                window_minutes=60,
                enabled=True,
                created_at=now(),
            )
        )
    async with engine.begin() as conn:
        monitor = dict((await conn.execute(
            select(monitor_configs).where(monitor_configs.c.id == monitor_id)
        )).mappings().one())
        rule = dict((await conn.execute(
            select(alert_rules).where(alert_rules.c.id == rule_id)
        )).mappings().one())
    return project_id, monitor, rule


async def emit(project_id, n: int, output: str, minutes_ago: int = 1):
    """Traffic. `ok` either way — drift is invisible to status and cost."""
    async with engine.begin() as conn:
        for i in range(n):
            await conn.execute(
                insert(traces).values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    name="ask",
                    status="ok",
                    input={"question": f"where is my order {uuid.uuid4().hex[:6]}?"},
                    output=output,
                    started_at=now() - timedelta(minutes=minutes_ago),
                    latency_ms=320,
                    total_cost_usd=0.0012,
                )
            )


async def mean_score(monitor_id) -> float | None:
    async with engine.begin() as conn:
        rows = await conn.execute(
            select(monitor_scores.c.scores).where(monitor_scores.c.monitor_id == monitor_id)
        )
    values = [
        float(entry["score"])
        for (scores,) in rows
        for entry in (scores or {}).values()
        if isinstance(entry, dict) and "error" not in entry and entry.get("score") is not None
    ]
    return (sum(values) / len(values)) if values else None


async def events(rule_id) -> list[dict]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            select(alert_events).where(alert_events.c.rule_id == rule_id).order_by(alert_events.c.fired_at)
        )
        return [dict(r) for r in rows.mappings()]


async def main() -> int:
    load_graders()
    load_channels()

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webhook_url = f"http://127.0.0.1:{server.server_port}/hook"

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label} {detail}")

    try:
        project_id, monitor, rule = await setup(webhook_url)

        print("\n=== 1. healthy traffic ===")
        await emit(project_id, 20, GOOD)
        scored = await run_monitor(monitor)
        healthy = await mean_score(monitor["id"])
        check("monitor sampled healthy traffic", scored == 20, f"scored {scored}")
        check("score is high", healthy == 1.0, f"mean {healthy}")
        check("no alert while healthy", await evaluate(rule) is None)

        print("\n=== 2. drift ===")
        # Degraded answers. Still status=ok, still costing money: nothing but
        # grading can see this.
        await emit(project_id, 60, BAD)
        scored = await run_monitor(monitor)
        drifted = await mean_score(monitor["id"])
        check("monitor sampled the drifted traffic", scored > 0, f"scored {scored}")
        check("score fell below the threshold", drifted is not None and drifted < 0.5,
              f"mean {drifted:.3f}" if drifted is not None else "no score")

        outcome = await evaluate(rule)
        check("alert fired within one cycle", outcome == "fired", f"outcome {outcome}")
        opened = await events(rule["id"])
        check("exactly one event opened", len(opened) == 1, f"{len(opened)} events")
        check("event was marked notified", bool(opened and opened[0]["notified"]))

        print("\n=== 3. webhook delivery ===")
        check("webhook received the alert", len(RECEIVED) == 1, f"{len(RECEIVED)} deliveries")
        if RECEIVED:
            payload = RECEIVED[0]
            check("payload names the event", payload.get("event") == "alert.fired", str(payload.get("event")))
            check("payload carries the rule", payload.get("rule", {}).get("metric") == "eval_score")
            check("payload carries the breaching value", payload.get("value") is not None,
                  f"value {payload.get('value')}")

        print("\n=== 4. no re-fire while open ===")
        again = await evaluate(rule)
        check("second cycle does not duplicate", again is None, f"outcome {again}")
        check("still one event", len(await events(rule["id"])) == 1)

        print("\n=== 5. recovery ===")
        # Enough good traffic to pull the windowed mean back over the threshold.
        await emit(project_id, 200, GOOD)
        await run_monitor(monitor)
        recovered = await mean_score(monitor["id"])
        outcome = await evaluate(rule)
        check("alert resolved", outcome == "resolved",
              f"outcome {outcome}, mean {recovered:.3f}" if recovered is not None else "no score")
        closed = await events(rule["id"])
        check("the event was closed, not duplicated",
              len(closed) == 1 and closed[0]["resolved_at"] is not None)
        check("webhook received the resolution", len(RECEIVED) == 2, f"{len(RECEIVED)} deliveries")
        if len(RECEIVED) == 2:
            check("resolution payload is labelled", RECEIVED[1].get("event") == "alert.resolved",
                  str(RECEIVED[1].get("event")))
    finally:
        server.shutdown()
        await engine.dispose()

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 8 acceptance PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
