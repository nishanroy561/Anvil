"""Breadth check: exercise every user-facing feature over real HTTP.

    docker compose up -d --wait          # or the dev stack
    python scripts/feature_sweep.py      # exits 0 when everything responds

Provisions its own tenant, drives every surface the dashboard and the SDKs use,
and removes the tenant on the way out (`--keep` to leave it). The second tenant it
creates is deliberate: cross-tenant reads must 404, and proving that needs
somebody else to be refused.

**This is breadth, not depth, and the distinction matters.** The phase acceptance
scripts prove specific hard properties — zero loss through a worker kill, the
comparison refusing to call a 4% shift from eight examples, a deferred vote
landing exactly once. This asks a shallower question of far more surface: does
every feature respond correctly at all. Both are needed; neither replaces the
other.

It exists because a full pass found two things nothing else did: the API image
shipped without `croniter`, so cron validation silently no-opped in the only
place it mattered, and an unpersistable Redis entry could crash-loop the worker.
Neither was visible from the unit suites, which pass in an environment where the
API and worker requirements happen to share a virtualenv.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tenants import DEFAULT_DATABASE_URL, drop_tenant_sync  # noqa: E402

BASE = "http://localhost:8000"
PASSWORD = "feature-sweep-pw-2026"

results: list[tuple[str, bool, str]] = []
SECTION = {"name": ""}


def section(name: str) -> None:
    SECTION["name"] = name
    print(f"\n=== {name} ===")


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((f"{SECTION['name']}: {label}", bool(ok), str(detail)))
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' - ' + str(detail)[:120]) if detail else ''}")
    return bool(ok)


def wait_for(fn, timeout=45.0, interval=1.0):
    """Poll until fn() is truthy. The write path is asynchronous by design."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def make_trace(name="sweep", question="how long do refunds take?",
               answer="Refunds take 5-7 days.", status="ok",
               model="claude-sonnet-5", meta=None):
    now = time.time()
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "status": status,
        "input": {"question": question},
        "output": answer,
        "metadata": meta or {"tier": "pro"},
        "model": model,
        "started_at": _iso(now - 1),
        "ended_at": _iso(now),
        "spans": [{
            "id": str(uuid.uuid4()), "parent_span_id": None, "name": model, "type": "llm",
            "status": "ok", "input": question, "output": answer,
            "attributes": {"model": model}, "tokens_in": 100, "tokens_out": 20,
            "started_at": _iso(now - 1), "ended_at": _iso(now),
        }],
    }


def main() -> int:  # noqa: C901 - a linear script; splitting it would hide the order
    try:
        health = httpx.get(f"{BASE}/healthz", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot reach {BASE}: {exc}")
        print("start it with: docker compose up -d --wait")
        return 3
    if not health.get("db") or not health.get("redis"):
        print(f"stack unhealthy: {health}")
        return 3

    c = httpx.Client(base_url=BASE, timeout=60.0)
    email = f"sweep-{uuid.uuid4().hex[:8]}@example.com"
    project_id = None
    other_project_id = None

    try:
        # ---- auth -----------------------------------------------------------
        section("auth")
        r = c.post("/v1/auth/signup", json={"email": email, "password": PASSWORD,
                                            "org_name": "Feature Sweep"})
        check("signup returns 2xx", r.status_code < 300, r.status_code)
        check("session cookie set", "anvil_session" in c.cookies, list(c.cookies.keys()))

        me = c.get("/v1/auth/me")
        check("GET /auth/me identifies the user",
              me.status_code == 200 and me.json().get("email") == email, me.text[:80])

        dup = httpx.post(f"{BASE}/v1/auth/signup",
                         json={"email": email, "password": PASSWORD, "org_name": "x"}, timeout=30)
        check("duplicate email refused", dup.status_code >= 400, dup.status_code)

        bad = httpx.post(f"{BASE}/v1/auth/login",
                         json={"email": email, "password": "wrong"}, timeout=30)
        check("wrong password 401s", bad.status_code == 401, bad.status_code)

        check("no cookie 401s", httpx.get(f"{BASE}/v1/projects", timeout=30).status_code == 401)

        # ---- projects -------------------------------------------------------
        section("projects")
        projects = c.get("/v1/projects").json()
        check("signup created a default project", len(projects) >= 1, len(projects))
        project_id = projects[0]["id"]
        check("create project", c.post("/v1/projects", json={"name": "Second"}).status_code < 300)
        check("project list grew", len(c.get("/v1/projects").json()) == len(projects) + 1)

        # ---- api keys -------------------------------------------------------
        section("api keys")
        created = c.post(f"/v1/projects/{project_id}/api-keys", json={"name": "sweep"})
        key = created.json().get("key")
        check("create returns the full key once",
              bool(key) and key.startswith("ak_"), created.status_code)
        listed = c.get(f"/v1/projects/{project_id}/api-keys").json()
        check("list returns prefixes only, never the secret",
              all("key" not in k for k in listed), json.dumps(listed)[:90])

        throwaway = c.post(f"/v1/projects/{project_id}/api-keys",
                           json={"name": "revoke-me"}).json()
        check("revoke a key", c.delete(f"/v1/api-keys/{throwaway['id']}").status_code < 300)
        used = httpx.post(f"{BASE}/v1/ingest/traces", json={"traces": [make_trace()]},
                          headers={"Authorization": f"Bearer {throwaway['key']}"}, timeout=30)
        check("revoked key is rejected", used.status_code == 401, used.status_code)

        # ---- ingest ---------------------------------------------------------
        section("ingest")
        auth = {"Authorization": f"Bearer {key}"}
        batch = [
            make_trace(name="ask", question="refund?", answer="Refunds take 5-7 days."),
            make_trace(name="ask", question="shipping?", answer="Shipping is 3-5 days."),
            make_trace(name="ask", question="2fa?", answer="I'm sorry, I don't know."),
            make_trace(name="ask", question="invoices?", answer="Invoices go out on the 1st."),
            make_trace(name="ask", question="limits?", answer="I'm sorry, I can't help."),
            make_trace(name="broken", question="boom?", answer="err", status="error",
                       model="gpt-4o-mini", meta={"tier": "free"}),
        ]
        ing = httpx.post(f"{BASE}/v1/ingest/traces", json={"traces": batch},
                         headers=auth, timeout=30)
        check("batch accepted 202", ing.status_code == 202, ing.status_code)
        check("accepted count echoed", ing.json().get("accepted") == len(batch), ing.text[:60])
        check("malformed batch 422s",
              httpx.post(f"{BASE}/v1/ingest/traces", json={"traces": [{"nope": 1}]},
                         headers=auth, timeout=30).status_code == 422)
        check("project_id in the payload is unexpressible",
              httpx.post(f"{BASE}/v1/ingest/traces",
                         json={"traces": [dict(make_trace(), project_id=str(uuid.uuid4()))]},
                         headers=auth, timeout=30).status_code == 422)
        stats = httpx.get(f"{BASE}/v1/ingest/stats", headers=auth, timeout=30)
        check("ingest stats", stats.status_code == 200 and "counters" in stats.json())

        persisted = wait_for(lambda: (lambda n: n if n >= len(batch) else None)(
            len(c.get(f"/v1/projects/{project_id}/traces?limit=50").json()["items"])))
        check("worker persisted the batch", persisted is not None, f"{persisted} traces")

        # ---- trace reads ----------------------------------------------------
        section("trace reads")
        items = c.get(f"/v1/projects/{project_id}/traces?limit=50").json()["items"]
        check("list returns traces", len(items) >= 6, len(items))
        check("cost derived from the price table", any(t.get("total_cost_usd") for t in items))
        check("filter by status",
              len(c.get(f"/v1/projects/{project_id}/traces?status=error").json()["items"]) == 1)
        check("filter by name",
              len(c.get(f"/v1/projects/{project_id}/traces?name=broken").json()["items"]) == 1)
        check("filter by model",
              len(c.get(f"/v1/projects/{project_id}/traces?model=gpt-4o-mini")
                  .json()["items"]) == 1)
        check("filter by metadata containment",
              len(c.get(f"/v1/projects/{project_id}/traces?metadata.tier=free")
                  .json()["items"]) == 1)
        check("free-text search",
              len(c.get(f"/v1/projects/{project_id}/traces?q=shipping").json()["items"]) >= 1)
        check("search below the trigram floor is refused",
              c.get(f"/v1/projects/{project_id}/traces?q=ab").status_code == 400)

        page1 = c.get(f"/v1/projects/{project_id}/traces?limit=2").json()
        check("pagination returns a cursor", bool(page1.get("next_cursor")))
        page2 = c.get(
            f"/v1/projects/{project_id}/traces?limit=2&cursor={page1['next_cursor']}").json()
        check("pages do not overlap",
              not ({t["id"] for t in page1["items"]} & {t["id"] for t in page2["items"]}))
        check("malformed cursor 400s",
              c.get(f"/v1/projects/{project_id}/traces?cursor=nope").status_code == 400)

        detail = c.get(f"/v1/traces/{items[0]['id']}").json()
        check("trace detail carries spans", len(detail.get("spans", [])) >= 1)
        check("unknown trace 404s", c.get(f"/v1/traces/{uuid.uuid4()}").status_code == 404)

        # ---- metrics --------------------------------------------------------
        section("metrics")
        overview = c.get(f"/v1/projects/{project_id}/metrics/overview").json()
        check("overview totals", overview.get("total_traces", 0) >= 6,
              overview.get("total_traces"))
        check("error rate computed", overview.get("error_rate", 0) > 0,
              overview.get("error_rate"))
        check("buckets returned", len(overview.get("buckets", [])) >= 1)
        check("p95 latency present", overview.get("p95_latency_ms") is not None)

        # ---- feedback + failure inbox ---------------------------------------
        section("feedback")
        target = items[0]
        for t in items:
            if "sorry" in json.dumps(t).lower():
                target = t
                break
        check("feedback accepted 202",
              httpx.post(f"{BASE}/v1/feedback",
                         json={"trace_id": target["id"], "rating": -1,
                               "comment": "this was wrong", "user_ref": "user-1"},
                         headers=auth, timeout=30).status_code == 202)
        landed = wait_for(lambda: c.get(
            f"/v1/projects/{project_id}/traces?feedback=negative").json()["items"] or None)
        check("negative feedback filter finds it", bool(landed), len(landed or []))

        inbox = c.get(f"/v1/projects/{project_id}/failure-inbox").json()
        check("failure inbox lists it", inbox.get("total", 0) >= 1, inbox.get("total"))
        check("inbox carries the comment",
              any("wrong" in json.dumps(i) for i in inbox.get("items", [])))
        check("trace detail carries feedback events",
              len(c.get(f"/v1/traces/{target['id']}").json().get("feedback_events", [])) >= 1)

        httpx.post(f"{BASE}/v1/feedback",
                   json={"trace_id": target["id"], "rating": 1, "user_ref": "user-1"},
                   headers=auth, timeout=30)
        flipped = wait_for(lambda: (lambda d: d if d.get("feedback_events")
                                    and d["feedback_events"][0]["rating"] == 1 else None)(
            c.get(f"/v1/traces/{target['id']}").json()))
        check("the same user toggling leaves one row, not two",
              flipped is not None and len(flipped.get("feedback_events", [])) == 1,
              len((flipped or {}).get("feedback_events", [])))

        # ---- datasets -------------------------------------------------------
        section("datasets")
        ds = c.post(f"/v1/projects/{project_id}/datasets",
                    json={"name": "Sweep dataset", "description": "d"}).json()
        check("create dataset", bool(ds.get("id")))
        promo = c.post(f"/v1/datasets/{ds['id']}/promote",
                       json={"trace_ids": [t["id"] for t in items]}).json()
        check("promote traces", promo.get("promoted", 0) >= 6, promo)
        check("re-promoting is a no-op",
              c.post(f"/v1/datasets/{ds['id']}/promote",
                     json={"trace_ids": [t["id"] for t in items]}).json().get("promoted") == 0)

        ditems = c.get(f"/v1/datasets/{ds['id']}/items").json()
        check("list items", ditems.get("total", 0) >= 6, ditems.get("total"))
        check("promotion kept the recorded output (ADR-0018)",
              any((i.get("metadata") or {}).get("recorded_output") is not None
                  for i in ditems["items"]))

        patched = c.patch(f"/v1/dataset-items/{ditems['items'][0]['id']}",
                          json={"expected_output": "edited reference"})
        check("edit an item's expected output", patched.status_code == 200)
        check("editing flags expected_output_edited",
              (patched.json().get("metadata") or {}).get("expected_output_edited") is True)

        added = c.post(f"/v1/datasets/{ds['id']}/items",
                       json={"items": [{"input": {"q": "manual"}, "expected_output": "a"}]})
        check("add an item by hand", added.status_code < 300, added.status_code)
        check("delete an item",
              c.delete(f"/v1/dataset-items/{added.json()[0]['id']}").status_code < 300)

        # ---- graders + eval runs --------------------------------------------
        section("eval runs")
        graders = c.get("/v1/graders").json()
        check("grader catalog served", len(graders) >= 10, len(graders))
        check("catalog carries config schemas", all("config_schema" in g for g in graders))
        check("empty grader config refused (the silent-pass guard)",
              c.post("/v1/eval-runs", json={
                  "dataset_id": ds["id"], "name": "bad", "target": {"mode": "rescore"},
                  "graders": [{"name": "contains", "config": {}}]}).status_code == 400)

        run = c.post("/v1/eval-runs", json={
            "dataset_id": ds["id"], "name": "sweep-a", "target": {"mode": "rescore"},
            "graders": [{"name": "not_contains", "config": {"value": "I'm sorry"}}]}).json()
        check("create eval run", bool(run.get("id")), run.get("status"))
        done = wait_for(lambda: (lambda r: r if r["status"] in ("completed", "failed") else None)(
            c.get(f"/v1/eval-runs/{run['id']}").json()), timeout=90)
        check("run completes", done and done["status"] == "completed", (done or {}).get("status"))
        g = ((done or {}).get("summary") or {}).get("graders", {}).get("not_contains") or {}
        check("summary has a mean", g.get("mean") is not None, g.get("mean"))
        check("refusals scored 0 and answers 1", 0 < (g.get("mean") or 0) < 1, g.get("mean"))
        check("per-item results",
              c.get(f"/v1/eval-runs/{run['id']}/results").json().get("total", 0) >= 6)
        check("run list", len(c.get(f"/v1/projects/{project_id}/eval-runs").json()) >= 1)

        run2 = c.post("/v1/eval-runs", json={
            "dataset_id": ds["id"], "name": "sweep-cancel", "target": {"mode": "rescore"},
            "graders": [{"name": "contains", "config": {"value": "a"}}]}).json()
        cancelled = c.post(f"/v1/eval-runs/{run2['id']}/cancel")
        check("cancel a run",
              cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled")
        check("cancelling twice 409s",
              c.post(f"/v1/eval-runs/{run2['id']}/cancel").status_code == 409)

        run3 = c.post("/v1/eval-runs", json={
            "dataset_id": ds["id"], "name": "sweep-b", "target": {"mode": "rescore"},
            "graders": [{"name": "not_contains", "config": {"value": "I'm sorry"}}]}).json()
        wait_for(lambda: (lambda r: r if r["status"] in ("completed", "failed") else None)(
            c.get(f"/v1/eval-runs/{run3['id']}").json()), timeout=90)
        cmp_ = c.get(f"/v1/eval-runs/compare?a={run['id']}&b={run3['id']}")
        check("compare two runs", cmp_.status_code == 200, cmp_.status_code)
        body = cmp_.json() if cmp_.status_code == 200 else {}
        check("compare reports the paired item count", body.get("compared_items", 0) >= 6)
        verdicts = [x.get("verdict") for x in body.get("graders", [])]
        check("small n reads inconclusive (ADR-0008)",
              bool(verdicts) and all(v == "inconclusive" for v in verdicts), verdicts)
        check("deleting a dataset with runs 409s",
              c.delete(f"/v1/datasets/{ds['id']}").status_code == 409)

        # ---- monitors -------------------------------------------------------
        section("monitors")
        mon = c.post(f"/v1/projects/{project_id}/monitors", json={
            "name": "refusal rate", "sample_rate": 1.0, "schedule_cron": "*/10 * * * *",
            "graders": [{"name": "not_contains", "config": {"value": "I'm sorry"}}]})
        check("create monitor", mon.status_code < 300, mon.status_code)
        # A monitor that never runs looks exactly like one finding nothing wrong.
        check("unparseable cron refused",
              c.post(f"/v1/projects/{project_id}/monitors", json={
                  "name": "bad", "sample_rate": 1.0, "schedule_cron": "every ten minutes",
                  "graders": [{"name": "not_contains", "config": {"value": "x"}}]
              }).status_code == 400)
        if mon.status_code < 300:
            mon_id = mon.json()["id"]
            check("list monitors",
                  len(c.get(f"/v1/projects/{project_id}/monitors").json()) >= 1)
            check("disable a monitor",
                  c.patch(f"/v1/monitors/{mon_id}", json={"enabled": False}).json()["enabled"]
                  is False)
            check("monitor scores endpoint",
                  c.get(f"/v1/monitors/{mon_id}/scores").status_code == 200)
            check("monitor samples endpoint",
                  c.get(f"/v1/monitors/{mon_id}/samples").status_code == 200)
            check("delete a monitor", c.delete(f"/v1/monitors/{mon_id}").status_code < 300)

        # ---- alerts ---------------------------------------------------------
        section("alerts")
        rule = c.post(f"/v1/projects/{project_id}/alert-rules", json={
            "name": "cost too high", "metric": "cost", "comparison": "gt",
            "threshold": 100, "window_minutes": 60})
        check("create alert rule", rule.status_code < 300, rule.status_code)
        check("unknown metric refused",
              c.post(f"/v1/projects/{project_id}/alert-rules", json={
                  "name": "nope", "metric": "not_a_metric", "comparison": "gt",
                  "threshold": 1, "window_minutes": 60}).status_code >= 400)
        if rule.status_code < 300:
            rule_id = rule.json()["id"]
            check("list alert rules",
                  len(c.get(f"/v1/projects/{project_id}/alert-rules").json()) >= 1)
            check("update a rule",
                  c.patch(f"/v1/alert-rules/{rule_id}", json={"enabled": False}).status_code == 200)
            check("alert events endpoint",
                  c.get(f"/v1/projects/{project_id}/alert-events").status_code == 200)
            check("set alert webhook",
                  c.patch(f"/v1/projects/{project_id}/alert-webhook",
                          json={"alert_webhook_url": "http://example.invalid/hook"}
                          ).status_code == 200)
            check("read the webhook back",
                  c.get(f"/v1/projects/{project_id}/alert-webhook").json()
                  .get("alert_webhook_url") == "http://example.invalid/hook")
            check("delete a rule", c.delete(f"/v1/alert-rules/{rule_id}").status_code < 300)

        # ---- usage ----------------------------------------------------------
        section("usage")
        usage = c.get("/v1/usage")
        check("usage endpoint", usage.status_code == 200, usage.status_code)
        keys = set(usage.json().keys()) if usage.status_code == 200 else set()
        # A `limit` here is the seed of a quota bar, and Anvil has no tiers.
        check("no quota or plan fields (ADR-0019)",
              not (keys & {"limit", "quota", "plan", "percent_of_limit"}), sorted(keys))

        # ---- tenancy --------------------------------------------------------
        section("tenancy")
        other = httpx.Client(base_url=BASE, timeout=30.0)
        other.post("/v1/auth/signup",
                   json={"email": f"other-{uuid.uuid4().hex[:6]}@example.com",
                         "password": PASSWORD, "org_name": "Sweep Other Org"})
        other_project_id = other.get("/v1/projects").json()[0]["id"]
        check("another tenant gets 404 on our project, never 403",
              other.get(f"/v1/projects/{project_id}/traces").status_code == 404)
        check("another tenant gets 404 on our trace",
              other.get(f"/v1/traces/{items[0]['id']}").status_code == 404)
        check("another tenant gets 404 on our dataset",
              other.get(f"/v1/datasets/{ds['id']}").status_code == 404)
        other.close()

        # ---- session --------------------------------------------------------
        section("session")
        check("logout", c.post("/v1/auth/logout").status_code < 300)
        check("the session is dead afterwards", c.get("/v1/projects").status_code == 401)
    finally:
        c.close()
        if "--keep" in sys.argv:
            print(f"\n(kept: projects {project_id}, {other_project_id})")
        else:
            print("\n=== cleaning up ===")
            for pid in (project_id, other_project_id):
                if pid:
                    try:
                        drop_tenant_sync(DEFAULT_DATABASE_URL, pid)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  (could not remove {pid}: {exc})")

    print()
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("\nFAILED:")
        for label, _, detail in failed:
            print(f"  - {label}  ({detail})")
        return 1
    print("every feature responded correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
