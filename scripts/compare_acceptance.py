"""Phase 6 acceptance: compare two seeded runs with a known shift.

    docker compose up -d postgres redis
    cd apps/api  && python -m uvicorn anvil_api.main:app --port 8000
    cd apps/worker && python -m anvil_worker.main
    python scripts/compare_acceptance.py        # exits 0 on pass

Three comparisons, run against a live API + worker:

  1. n=40 with a real shift    -> must be called (significant, correct delta/CI)
  2. n=5  with the same shift  -> must read "inconclusive"
  3. a run compared with itself -> must NOT read as "no regression"

The shift is produced with the deterministic `contains` grader over rescore
runs: run A's recorded outputs are refusals ("I'm sorry, I don't know"), run B's
are real answers. `contains` grades the output alone, so nothing here is
tautological and **no judge credits are spent** — the statistics do not care
where the scores came from, so the acceptance does not need a provider key.

Note what case 2 demonstrates: the shift in the n=5 run is exactly as large as
the one in the n=40 run and is genuinely there. The verdict still declines,
because five items cannot establish it. That refusal is the feature.
"""

import sys
import time

import uuid

import httpx

from _tenants import DEFAULT_DATABASE_URL, drop_tenant_sync

BASE = "http://localhost:8000"
PASSWORD = "compare-acceptance-pw"

REFUSAL = "I'm sorry, I don't know the answer to that."
ANSWER = "The deployment finished at 14:02 UTC and all checks passed."


def new_tenant() -> tuple[httpx.Client, str]:
    """Its own org and project, torn down at the end.

    This used to log in as the seeded demo user and take `/v1/projects[0]`,
    which had two costs: the datasets and eval runs it creates piled up inside
    real demo data where no teardown could safely tell them apart, and the
    acceptance could only run on a box where `python -m anvil_api.seed` had been
    run first. A fresh signup fixes both.
    """
    c = httpx.Client(base_url=BASE, timeout=30.0)
    email = f"compare-{uuid.uuid4().hex[:8]}@example.com"
    c.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "org_name": "Compare Acceptance"},
    ).raise_for_status()
    return c, c.get("/v1/projects").json()[0]["id"]


def make_dataset(c: httpx.Client, pid: str, name: str) -> str:
    r = c.post(f"/v1/projects/{pid}/datasets", json={"name": name})
    r.raise_for_status()
    return r.json()["id"]


def add_items(c: httpx.Client, ds: str, n: int, recorded: str) -> list[str]:
    """Items whose *recorded* output is what gets graded in rescore mode."""
    body = {
        "items": [
            {
                "input": {"question": f"what happened in deploy {i}?"},
                "expected_output": "a truthful description of the deploy",
                "metadata": {"recorded_output": recorded},
            }
            for i in range(n)
        ]
    }
    r = c.post(f"/v1/datasets/{ds}/items", json=body)
    r.raise_for_status()
    return [i["id"] for i in r.json()]


def set_recorded(c: httpx.Client, item_ids: list[str], recorded: str) -> None:
    for item_id in item_ids:
        r = c.patch(
            f"/v1/dataset-items/{item_id}",
            json={"metadata": {"recorded_output": recorded}},
        )
        r.raise_for_status()


def run_eval(c: httpx.Client, ds: str, name: str) -> str:
    r = c.post(
        "/v1/eval-runs",
        json={
            "dataset_id": ds,
            "name": name,
            "target": {"mode": "rescore"},
            # "did the assistant refuse?" - graded on the output alone.
            "graders": [{"name": "contains", "config": {"value": "I'm sorry"}}],
        },
    )
    r.raise_for_status()
    return r.json()["id"]


def wait(c: httpx.Client, run_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = c.get(f"/v1/eval-runs/{run_id}").json()
        if run["status"] in ("completed", "failed", "cancelled"):
            return run
        time.sleep(1.0)
    raise TimeoutError(f"run {run_id} still {run['status']}")


def compare(c: httpx.Client, a: str, b: str) -> dict:
    r = c.get("/v1/eval-runs/compare", params={"a": a, "b": b})
    r.raise_for_status()
    return r.json()


def scenario(c: httpx.Client, pid: str, n: int, label: str) -> dict:
    ds = make_dataset(c, pid, f"phase6-{label}-{int(time.time())}")
    items = add_items(c, ds, n, REFUSAL)

    run_a = run_eval(c, ds, f"{label}-A-refusing")
    a = wait(c, run_a)
    print(f"  run A ({label}): {a['status']}, "
          f"contains mean={a['summary']['graders']['contains']['mean']}")

    # The "change": the assistant stopped refusing.
    set_recorded(c, items, ANSWER)

    run_b = run_eval(c, ds, f"{label}-B-answering")
    b = wait(c, run_b)
    print(f"  run B ({label}): {b['status']}, "
          f"contains mean={b['summary']['graders']['contains']['mean']}")

    return compare(c, run_a, run_b) | {"_run_a": run_a, "_run_b": run_b}


def show(out: dict) -> dict:
    g = out["graders"][0]
    print(f"  compared_items={out['compared_items']} partial={out['partial']}")
    print(f"  {g['grader']}: mean_a={g['mean_a']} mean_b={g['mean_b']} "
          f"delta={g['delta']}")
    print(f"  ci={g['ci']} p={g['p_value']} n={g['n']}")
    print(f"  verdict={g['verdict']} significant={g['significant']} "
          f"reason={g['reason']}")
    return g


def main() -> int:
    c, pid = new_tenant()
    try:
        return run_checks(c, pid)
    finally:
        c.close()
        if "--keep" in sys.argv:
            print(f"\n(kept: project {pid})")
        else:
            print("\n=== cleaning up the acceptance tenant ===")
            drop_tenant_sync(DEFAULT_DATABASE_URL, pid)


def run_checks(c: httpx.Client, pid: str) -> int:
    failures = []

    print("\n=== 1. n=40, real shift: must be called ===")
    big = scenario(c, pid, 40, "n40")
    g = show(big)
    if g["verdict"] != "regressed":
        failures.append(f"n=40 verdict was {g['verdict']}, expected 'regressed'")
    if not g["significant"]:
        failures.append("n=40 was not significant")
    if g["delta"] != -1.0:
        failures.append(f"n=40 delta was {g['delta']}, expected -1.0")
    if g["ci"][1] >= 0:
        failures.append(f"n=40 CI {g['ci']} touches zero")
    if g["tautological_pairs"] != 0:
        failures.append(f"n=40 had {g['tautological_pairs']} tautological pairs")

    print("\n=== 2. n=5, same shift: must read inconclusive ===")
    small = scenario(c, pid, 5, "n5")
    g = show(small)
    if g["verdict"] != "inconclusive":
        failures.append(f"n=5 verdict was {g['verdict']}, expected 'inconclusive'")
    if g["reason"] != "insufficient_data":
        failures.append(f"n=5 reason was {g['reason']}")
    if g["delta"] != -1.0:
        failures.append(f"n=5 delta was {g['delta']} (the shift is real, just unprovable)")

    print("\n=== 3. a run against itself: must not read as 'no regression' ===")
    self_cmp = compare(c, big["_run_a"], big["_run_a"])
    g = show(self_cmp)
    if g["verdict"] != "inconclusive":
        failures.append(f"self-comparison verdict was {g['verdict']}")

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 6 acceptance PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
