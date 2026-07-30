"""`anvil` — the CI gate (plan/evaluation.md §5).

    anvil eval run --dataset <id> --config eval.yaml --wait \
        --min-score accuracy=0.8 --baseline-run <id>

Hosted in the SDK because CI already installs the SDK, and one dependency for
"instrument the app" and "gate the pipeline" is better than two.

**Exit codes are the product here.** A CI gate that cannot fail is decoration,
so the failure paths are the load-bearing ones:

    0  every threshold met, and no significant regression against the baseline
    1  a threshold failed, or a grader significantly regressed
    2  the run itself did not finish — failed, cancelled, or timed out
    3  the tool could not do its job: bad config, bad credentials, API down

1 and 2 are kept apart deliberately. "Quality dropped" is a verdict about the
change and should block the merge; "the eval never ran" is an infrastructure
problem and should be read differently by whoever looks at the red check.

Authentication is `ANVIL_API_KEY` — a project-scoped key (ADR-0021), not a
user's password.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

DEFAULT_BASE_URL = "http://localhost:8000"
POLL_INTERVAL_S = 3.0
DEFAULT_TIMEOUT_S = 1800.0

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_RUN_INCOMPLETE = 2
EXIT_USAGE = 3

TERMINAL = ("completed", "failed", "cancelled")


class CliError(Exception):
    """A problem with the invocation or environment — exits `EXIT_USAGE`."""


# ---- config -----------------------------------------------------------------


def load_config(path: str) -> dict:
    """Reads `eval.yaml`. YAML if PyYAML is installed, JSON otherwise.

    JSON is a subset of YAML, so a `.yaml` file containing JSON parses either
    way — which keeps the CLI usable with no extra dependency while letting
    anyone who wants comments and block strings `pip install anvil-sdk[cli]`.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise CliError(f"couldn't read config {path!r}: {exc}") from exc

    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(raw)
    except ImportError:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(
                f"{path} isn't valid JSON and PyYAML isn't installed, so YAML can't be "
                f"parsed. Install it with: pip install 'anvil-sdk[cli]'  ({exc})"
            ) from exc
    except Exception as exc:  # yaml.YAMLError
        raise CliError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise CliError(f"{path} must contain a mapping at the top level")
    return parsed


def build_run_body(config: dict, dataset_id: str, name: str | None) -> dict:
    """Turns `eval.yaml` into a `POST /v1/eval-runs` body.

    A prompt may be inline (`prompt:`) or a file (`prompt_file:`) — CI usually
    wants the file, so the prompt lives in the repo and the diff that triggers
    the gate is the same diff being evaluated.
    """
    target = dict(config.get("target") or {})
    prompt_file = target.pop("prompt_file", None)
    if prompt_file:
        if target.get("prompt"):
            raise CliError("target has both 'prompt' and 'prompt_file'; use one")
        try:
            with open(prompt_file, "r", encoding="utf-8") as fh:
                target["prompt"] = fh.read()
        except OSError as exc:
            raise CliError(f"couldn't read prompt_file {prompt_file!r}: {exc}") from exc

    target.setdefault("mode", "generate")
    if target["mode"] == "generate" and not target.get("model"):
        raise CliError("target.mode is 'generate' but no target.model is set")

    graders = config.get("graders") or []
    if not graders:
        raise CliError("config lists no graders, so the run would measure nothing")
    normalised = []
    for g in graders:
        if isinstance(g, str):
            normalised.append({"name": g, "config": {}})
        elif isinstance(g, dict) and "name" in g:
            normalised.append({"name": g["name"], "config": g.get("config") or {}})
        else:
            raise CliError(f"grader entry {g!r} needs a 'name'")

    return {
        "dataset_id": dataset_id,
        "name": name or config.get("name") or f"ci-{int(time.time())}",
        "target": target,
        "graders": normalised,
    }


def parse_thresholds(pairs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CliError(f"--min-score expects grader=value, got {pair!r}")
        grader, _, value = pair.partition("=")
        try:
            out[grader.strip()] = float(value)
        except ValueError as exc:
            raise CliError(f"--min-score value in {pair!r} isn't a number") from exc
    return out


# ---- API --------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        import httpx

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def _check(self, res: Any) -> Any:
        if res.status_code >= 400:
            # `{"error": {"code", "message"}}` is the current shape; `detail` is
            # read too so the CLI keeps working against an older API, which
            # matters here more than anywhere else — CI pins SDK versions and
            # upgrades the server on its own schedule.
            try:
                body = res.json()
                detail = body.get("error") or body.get("detail") or body
                message = detail.get("message") if isinstance(detail, dict) else str(detail)
            except Exception:  # noqa: BLE001
                message = res.text[:300]
            request_id = res.headers.get("x-request-id")
            suffix = f" (request {request_id})" if request_id else ""
            raise CliError(
                f"{res.request.method} {res.request.url.path} -> "
                f"{res.status_code}: {message}{suffix}"
            )
        return res.json()

    def create_run(self, body: dict) -> dict:
        return self._check(self._client.post("/v1/eval-runs", json=body))

    def get_run(self, run_id: str) -> dict:
        return self._check(self._client.get(f"/v1/eval-runs/{run_id}"))

    def compare(self, a: str, b: str) -> dict:
        return self._check(self._client.get("/v1/eval-runs/compare", params={"a": a, "b": b}))

    def close(self) -> None:
        self._client.close()


def wait_for(api: Api, run_id: str, timeout_s: float, out) -> dict:
    """Polls to a terminal status, printing progress as it moves."""
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        run = api.get_run(run_id)
        if run["progress"] != last:
            last = run["progress"]
            print(f"  {run['status']}: {run['progress']}/{run['total']}", file=out, flush=True)
        if run["status"] in TERMINAL:
            return run
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"run {run_id} did not finish within {timeout_s:.0f}s")


# ---- reporting --------------------------------------------------------------


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def print_results(run: dict, out) -> None:
    summary = run.get("summary") or {}
    graders = summary.get("graders") or {}
    if not graders:
        print("  (no grader results)", file=out)
        return

    width = max(len(g) for g in graders)
    print(f"\n  {'grader'.ljust(width)}   mean   pass   scored  errored", file=out)
    for name, g in sorted(graders.items()):
        line = (
            f"  {name.ljust(width)}  {_fmt(g.get('mean'), 3):>5}  "
            f"{_fmt(g.get('pass_rate'), 2):>5}  {g.get('scored_items', 0):>6}  "
            f"{g.get('errored_items', 0):>7}"
        )
        if g.get("tautological_items"):
            # A mean built from self-comparisons is not a measurement, and a
            # gate that passes on one is worse than no gate (ADR-0018).
            line += f"   [{g['tautological_items']} graded against itself]"
        print(line, file=out)
    cost = summary.get("total_cost_usd")
    if cost:
        print(f"\n  cost ${cost:.6f}", file=out)


def check_thresholds(run: dict, thresholds: dict[str, float], out) -> list[str]:
    failures = []
    graders = (run.get("summary") or {}).get("graders") or {}
    for grader, minimum in thresholds.items():
        entry = graders.get(grader)
        if entry is None:
            failures.append(f"{grader}: no such grader in this run")
            continue
        mean = entry.get("mean")
        if mean is None:
            # Every item errored. Passing the gate because nothing was measured
            # is exactly the silent-pass failure this tool exists to prevent.
            failures.append(f"{grader}: no score (all {entry.get('errored_items', 0)} items errored)")
            continue
        if entry.get("tautological_items") and entry["tautological_items"] == entry.get("scored_items"):
            failures.append(f"{grader}: every item was graded against its own output - this measured nothing")
            continue
        if mean < minimum:
            failures.append(f"{grader}: {mean:.3f} < {minimum:.3f}")
    return failures


def check_regressions(comparison: dict, out) -> list[str]:
    """Fails only on a *significant* regression.

    An inconclusive verdict does not block the merge. That is the honest
    reading — the data could not establish a change — and a gate that blocked
    on noise would be turned off within a week (ADR-0008).
    """
    failures = []
    print(f"\n  compared on {comparison['compared_items']} items"
          + (" (partial - the dataset changed between runs)" if comparison.get("partial") else ""),
          file=out)
    for g in comparison.get("graders", []):
        verdict = g["verdict"]
        ci = g.get("ci")
        detail = f"{_fmt(g.get('mean_a'))} -> {_fmt(g.get('mean_b'))}"
        if verdict == "regressed":
            failures.append(
                f"{g['grader']} regressed {detail}"
                + (f" (95% CI {ci[0]:.3f}..{ci[1]:.3f})" if ci else "")
            )
            print(f"  {g['grader']}: REGRESSED {detail}", file=out)
        elif verdict == "improved":
            print(f"  {g['grader']}: improved {detail}", file=out)
        else:
            reason = g.get("reason") or "inconclusive"
            print(f"  {g['grader']}: inconclusive ({reason}) {detail}", file=out)
    return failures


# ---- command ----------------------------------------------------------------


def cmd_eval_run(args: argparse.Namespace, out) -> int:
    api_key = args.api_key or os.getenv("ANVIL_API_KEY")
    if not api_key:
        raise CliError("no API key: set ANVIL_API_KEY or pass --api-key")
    base_url = args.base_url or os.getenv("ANVIL_BASE_URL") or DEFAULT_BASE_URL

    config = load_config(args.config)
    dataset_id = args.dataset or config.get("dataset")
    if not dataset_id:
        raise CliError("no dataset: pass --dataset or set 'dataset' in the config")
    body = build_run_body(config, dataset_id, args.name)
    thresholds = parse_thresholds(args.min_score or [])

    api = Api(base_url, api_key)
    try:
        run = api.create_run(body)
        print(f"run {run['id']} created ({run['total']} items)", file=out)
        if not args.wait:
            # Nothing has been measured yet, so there is nothing to gate on.
            print("not waiting (--wait not given); no gate applied", file=out)
            return EXIT_OK

        try:
            run = wait_for(api, run["id"], args.timeout, out)
        except TimeoutError as exc:
            print(f"\nFAIL: {exc}", file=out)
            return EXIT_RUN_INCOMPLETE

        if run["status"] != "completed":
            print(f"\nFAIL: run {run['status']}", file=out)
            return EXIT_RUN_INCOMPLETE

        print_results(run, out)

        failures = check_thresholds(run, thresholds, out)
        if args.baseline_run:
            failures += check_regressions(api.compare(args.baseline_run, run["id"]), out)

        if failures:
            print("\nFAILED:", file=out)
            for f in failures:
                print(f"  - {f}", file=out)
            return EXIT_GATE_FAILED

        print("\nPASSED", file=out)
        return EXIT_OK
    finally:
        api.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil", description="Anvil CLI")
    sub = parser.add_subparsers(dest="group", required=True)

    ev = sub.add_parser("eval", help="evaluation commands").add_subparsers(
        dest="command", required=True
    )
    run = ev.add_parser("run", help="run an eval and gate on the result")
    run.add_argument("--dataset", help="dataset id (or set 'dataset' in the config)")
    run.add_argument("--config", default="eval.yaml", help="eval config file (default: eval.yaml)")
    run.add_argument("--name", help="run name (default: from config, else ci-<timestamp>)")
    run.add_argument("--wait", action="store_true", help="poll to completion and apply the gate")
    run.add_argument(
        "--min-score",
        action="append",
        metavar="GRADER=VALUE",
        help="fail if the grader's mean is below VALUE; repeatable",
    )
    run.add_argument(
        "--baseline-run",
        help="fail if any grader significantly regressed against this run",
    )
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds to wait")
    run.add_argument("--api-key", help="defaults to $ANVIL_API_KEY")
    run.add_argument("--base-url", help="defaults to $ANVIL_BASE_URL, else localhost:8000")
    run.set_defaults(func=cmd_eval_run)
    return parser


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except CliError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001
        # Anything unexpected is an infrastructure problem, not a verdict about
        # the change under test — never exit 1 for it.
        print(f"error: {type(exc).__name__}: {exc}", file=out)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
