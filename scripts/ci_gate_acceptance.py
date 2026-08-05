"""Phase 7 acceptance: the CI gate passes lenient and fails strict.

    docker compose up -d postgres redis
    cd apps/api    && python -m uvicorn anvil_api.main:app --port 8000
    cd apps/worker && python -m anvil_worker.main
    pip install -e "./sdks/python[cli]"
    python scripts/ci_gate_acceptance.py         # exits 0 on pass

Checks, all against a live API + worker via the real `anvil` CLI:

  1. lenient --min-score                       -> exit 0
  2. strict --min-score on the same run        -> exit 1
  3. a threshold naming a grader that isn't there -> exit 1, not a silent pass
  4. --baseline-run against a regressed run    -> exit 1
  5. --baseline-run where nothing moved        -> exit 0 (inconclusive != block)
  6. a bad API key                             -> exit 3, never 1

The runs are rescore + `contains`, so no provider credits are spent. The point
is the gate's behaviour, and the statistics do not care where scores came from.
"""

import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from _tenants import DEFAULT_DATABASE_URL, drop_tenant_sync

BASE = "http://localhost:8000"
PASSWORD = "ci-gate-acceptance-pw"
REFUSAL = "I'm sorry, I don't know."
ANSWER = "The deploy finished at 14:02 UTC."


def new_tenant() -> tuple[httpx.Client, str]:
    """Its own org and project, torn down at the end.

    This used to log in as the seeded demo user and take `/v1/projects[0]`, so
    every run left datasets and eval runs inside real demo data, and the
    acceptance only worked on a box that had been seeded first.
    """
    c = httpx.Client(base_url=BASE, timeout=30.0)
    email = f"ci-gate-{uuid.uuid4().hex[:8]}@example.com"
    c.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "org_name": "CI Gate Acceptance"},
    ).raise_for_status()
    return c, c.get("/v1/projects").json()[0]["id"]


def make_key(c: httpx.Client, project_id: str) -> str:
    res = c.post(f"/v1/projects/{project_id}/api-keys", json={"name": f"ci-gate-{int(time.time())}"})
    res.raise_for_status()
    return res.json()["key"]


def make_dataset(
    c: httpx.Client, project_id: str, n: int, recorded: str, refusal_rate: float = 1.0
) -> tuple[str, list[str]]:
    """`refusal_rate` sets what fraction of items carry the refusal text, which
    is exactly the `contains` mean the run will produce. A threshold test needs
    a mean strictly between 0 and 1 — against a mean of 1.000 no threshold
    below 1.0 can fail, which says nothing about the gate."""
    ds = c.post(f"/v1/projects/{project_id}/datasets", json={"name": f"ci-gate-{int(time.time_ns())}"})
    ds.raise_for_status()
    dataset_id = ds.json()["id"]
    refusals = round(n * refusal_rate)
    items = c.post(
        f"/v1/datasets/{dataset_id}/items",
        json={
            "items": [
                {
                    "input": {"question": f"q{i}"},
                    "expected_output": "a truthful answer",
                    "metadata": {"recorded_output": recorded if i < refusals else ANSWER},
                }
                for i in range(n)
            ]
        },
    )
    items.raise_for_status()
    return dataset_id, [i["id"] for i in items.json()]


def set_recorded(c: httpx.Client, item_ids: list[str], recorded: str) -> None:
    for item_id in item_ids:
        c.patch(f"/v1/dataset-items/{item_id}", json={"metadata": {"recorded_output": recorded}}).raise_for_status()


def write_config(tmp: Path, dataset_id: str) -> Path:
    path = tmp / "eval.yaml"
    path.write_text(
        json.dumps(
            {
                "dataset": dataset_id,
                "target": {"mode": "rescore"},
                # "did the assistant refuse?" - graded on the output alone, so
                # nothing here is tautological and nothing costs money.
                "graders": [{"name": "contains", "config": {"value": "I'm sorry"}}],
            }
        ),
        encoding="utf-8",
    )
    return path


def run_cli(key: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "anvil_sdk.cli", "eval", "run", "--api-key", key, "--base-url", BASE, *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def last_run_id(c: httpx.Client, project_id: str) -> str:
    return c.get(f"/v1/projects/{project_id}/eval-runs?limit=1").json()[0]["id"]


def main() -> int:
    c, project_id = new_tenant()
    try:
        return run_checks(c, project_id)
    finally:
        c.close()
        if "--keep" in sys.argv:
            print(f"\n(kept: project {project_id})")
        else:
            print("\n=== cleaning up the acceptance tenant ===")
            drop_tenant_sync(DEFAULT_DATABASE_URL, project_id)


def run_checks(c: httpx.Client, project_id: str) -> int:
    key = make_key(c, project_id)
    failures: list[str] = []

    def check(label: str, got: int, want: int, output: str) -> None:
        ok = got == want
        print(f"  [{'ok' if ok else 'FAIL'}] {label}: exit {got} (want {want})")
        if not ok:
            failures.append(f"{label}: exit {got}, expected {want}\n{output}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        print("\n=== thresholds ===")
        # Half the items refuse, so `contains` scores a mean of 0.500 — a real
        # number a threshold can sit either side of.
        mixed_ds, _ = make_dataset(c, project_id, 40, REFUSAL, refusal_rate=0.5)
        mixed_config = write_config(tmp, mixed_ds)

        code, out = run_cli(key, "--config", str(mixed_config), "--wait", "--min-score", "contains=0.4")
        check("lenient threshold (mean 0.5 >= 0.4)", code, 0, out)
        if "0.500" not in out:
            failures.append(f"expected a mean of 0.500, got:\n{out}")

        code, out = run_cli(key, "--config", str(mixed_config), "--wait", "--min-score", "contains=0.8")
        check("strict threshold (mean 0.5 < 0.8)", code, 1, out)

        code, out = run_cli(key, "--config", str(mixed_config), "--wait", "--min-score", "containz=0.1")
        check("typo'd grader name is not a silent pass", code, 1, out)

        print("\n=== regression gate ===")
        dataset_id, items = make_dataset(c, project_id, 40, REFUSAL)
        config = write_config(tmp, dataset_id)
        code, out = run_cli(key, "--config", str(config), "--wait")
        baseline_id = last_run_id(c, project_id)

        # The assistant stops refusing, so `contains "I'm sorry"` drops 1.0 -> 0.0.
        set_recorded(c, items, ANSWER)
        code, out = run_cli(key, "--config", str(config), "--wait", "--baseline-run", baseline_id)
        check("significant regression blocks", code, 1, out)
        if "regressed" not in out.lower():
            failures.append("regression output never said 'regressed'")

        print("\n=== inconclusive must not block ===")
        small_ds, _ = make_dataset(c, project_id, 5, REFUSAL)
        small_config = write_config(tmp, small_ds)
        code, out = run_cli(key, "--config", str(small_config), "--wait")
        small_baseline = last_run_id(c, project_id)
        code, out = run_cli(key, "--config", str(small_config), "--wait", "--baseline-run", small_baseline)
        check("n=5, no movement -> inconclusive, does not block", code, 0, out)

        print("\n=== credentials ===")
        code, out = run_cli("ak_deadbeef_nope", "--config", str(config), "--wait")
        check("bad API key is exit 3, never 1", code, 3, out)

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Phase 7 acceptance PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
