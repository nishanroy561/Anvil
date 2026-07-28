"""Eval CLI (plan/evaluation.md §5). No services, no keys — HTTP is mocked.

The exit codes are the deliverable. A gate that cannot fail is decoration, so
most of these tests are about *failing* correctly, and about the distinction
between "quality dropped" (1, block the merge) and "the eval never ran" (2,
go look at your infrastructure).
"""

import io
import json

import httpx
import pytest
import respx

from anvil_sdk import cli

BASE = "http://anvil.test"
KEY = "ak_test_key"

RUN_ID = "11111111-1111-1111-1111-111111111111"
BASELINE_ID = "22222222-2222-2222-2222-222222222222"
DATASET_ID = "33333333-3333-3333-3333-333333333333"


def write_config(tmp_path, **overrides):
    config = {
        "target": {"mode": "rescore"},
        "graders": [{"name": "accuracy", "config": {"threshold": 0.5}}],
    }
    config.update(overrides)
    path = tmp_path / "eval.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def run_row(status="completed", graders=None, progress=3, total=3):
    return {
        "id": RUN_ID,
        "status": status,
        "progress": progress,
        "total": total,
        "summary": {
            "graders": graders
            if graders is not None
            else {
                "accuracy": {
                    "mean": 0.9,
                    "pass_rate": 1.0,
                    "scored_items": 3,
                    "errored_items": 0,
                    "tautological_items": 0,
                }
            },
            "total_cost_usd": 0.0012,
        },
    }


def invoke(argv, out=None):
    out = out or io.StringIO()
    code = cli.main(argv, out=out)
    return code, out.getvalue()


def base_args(config, *extra):
    return [
        "eval", "run",
        "--dataset", DATASET_ID,
        "--config", config,
        "--api-key", KEY,
        "--base-url", BASE,
        *extra,
    ]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)


# ---- the gate ---------------------------------------------------------------


@respx.mock
def test_lenient_threshold_exits_zero(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "accuracy=0.8"))

    assert code == cli.EXIT_OK, out
    assert "PASSED" in out


@respx.mock
def test_strict_threshold_exits_one(tmp_path):
    """The Phase 7 acceptance in one test: same run, stricter bar, exit 1."""
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "accuracy=0.95"))

    assert code == cli.EXIT_GATE_FAILED, out
    assert "0.900 < 0.950" in out


@respx.mock
def test_a_threshold_on_an_absent_grader_fails_rather_than_passing(tmp_path):
    """Silently ignoring a typo'd grader name would make the gate a no-op while
    still going green — the worst outcome available."""
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "acuracy=0.5"))

    assert code == cli.EXIT_GATE_FAILED
    assert "no such grader" in out


@respx.mock
def test_an_all_errored_grader_fails_rather_than_passing(tmp_path):
    """mean=None means nothing was measured. Passing on that is a silent pass."""
    graders = {"accuracy": {"mean": None, "pass_rate": None, "scored_items": 0, "errored_items": 3}}
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json=run_row(graders=graders))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "accuracy=0.1"))

    assert code == cli.EXIT_GATE_FAILED
    assert "all 3 items errored" in out


@respx.mock
def test_a_wholly_tautological_grader_fails_the_gate(tmp_path):
    """A perfect score from grading output against itself is not a pass
    (ADR-0018) — the gate must not be satisfied by a measurement that
    could only ever have come back perfect."""
    graders = {
        "accuracy": {
            "mean": 1.0, "pass_rate": 1.0, "scored_items": 3,
            "errored_items": 0, "tautological_items": 3,
        }
    }
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json=run_row(graders=graders))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "accuracy=0.5"))

    assert code == cli.EXIT_GATE_FAILED
    assert "measured nothing" in out


# ---- regression gate --------------------------------------------------------


def comparison(verdict, reason=None, mean_a=0.9, mean_b=0.4):
    return {
        "compared_items": 40,
        "partial": False,
        "graders": [
            {
                "grader": "accuracy", "n": 40, "mean_a": mean_a, "mean_b": mean_b,
                "delta": mean_b - mean_a, "ci": [-0.6, -0.4], "p_value": 0.001,
                "significant": verdict != "inconclusive", "verdict": verdict,
                "reason": reason, "tautological_pairs": 0,
            }
        ],
    }


@respx.mock
def test_a_significant_regression_fails_the_gate(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/compare").mock(
        return_value=httpx.Response(200, json=comparison("regressed"))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--baseline-run", BASELINE_ID))

    assert code == cli.EXIT_GATE_FAILED, out
    assert "regressed" in out.lower()


@respx.mock
def test_an_inconclusive_verdict_does_not_block_the_merge(tmp_path):
    """Blocking on noise gets the gate switched off within a week. The honest
    reading of inconclusive is that no change was established (ADR-0008)."""
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/compare").mock(
        return_value=httpx.Response(200, json=comparison("inconclusive", reason="insufficient_data"))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--baseline-run", BASELINE_ID))

    assert code == cli.EXIT_OK, out
    assert "inconclusive" in out


@respx.mock
def test_an_improvement_passes(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/compare").mock(
        return_value=httpx.Response(200, json=comparison("improved", mean_a=0.4, mean_b=0.9))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--baseline-run", BASELINE_ID))
    assert code == cli.EXIT_OK, out


# ---- run didn't finish is not the same as quality dropped -------------------


@respx.mock
def test_a_failed_run_exits_two_not_one(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json=run_row(status="failed"))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--min-score", "accuracy=0.1"))

    assert code == cli.EXIT_RUN_INCOMPLETE, out
    assert "run failed" in out


@respx.mock
def test_a_run_that_never_finishes_times_out_to_two(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    respx.get(f"{BASE}/v1/eval-runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json=run_row(status="running", progress=1))
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait", "--timeout", "0"))

    assert code == cli.EXIT_RUN_INCOMPLETE
    assert "did not finish" in out


@respx.mock
def test_an_api_error_exits_three_not_one(tmp_path):
    """A 500 is an infrastructure problem, not a verdict about the change."""
    respx.post(f"{BASE}/v1/eval-runs").mock(
        return_value=httpx.Response(500, json={"detail": {"message": "boom"}})
    )

    code, out = invoke(base_args(write_config(tmp_path), "--wait"))

    assert code == cli.EXIT_USAGE, out
    assert "boom" in out


@respx.mock
def test_without_wait_nothing_is_gated(tmp_path):
    respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
    code, out = invoke(base_args(write_config(tmp_path), "--min-score", "accuracy=0.99"))
    assert code == cli.EXIT_OK
    assert "no gate applied" in out


# ---- config and usage -------------------------------------------------------


def test_a_missing_api_key_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_API_KEY", raising=False)
    code, out = invoke(
        ["eval", "run", "--dataset", DATASET_ID, "--config", write_config(tmp_path), "--base-url", BASE]
    )
    assert code == cli.EXIT_USAGE
    assert "ANVIL_API_KEY" in out


def test_a_missing_config_file_is_a_usage_error():
    code, out = invoke(base_args("does-not-exist.yaml"))
    assert code == cli.EXIT_USAGE
    assert "couldn't read config" in out


def test_a_config_with_no_graders_is_refused(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(json.dumps({"target": {"mode": "rescore"}, "graders": []}), encoding="utf-8")
    code, out = invoke(base_args(str(path)))
    assert code == cli.EXIT_USAGE
    assert "measure nothing" in out


def test_generate_mode_without_a_model_is_refused(tmp_path):
    config = write_config(tmp_path, target={"mode": "generate"})
    code, out = invoke(base_args(config))
    assert code == cli.EXIT_USAGE
    assert "target.model" in out


def test_a_bare_grader_name_is_accepted(tmp_path):
    body = cli.build_run_body({"graders": ["valid_json"], "target": {"mode": "rescore"}}, "d", None)
    assert body["graders"] == [{"name": "valid_json", "config": {}}]


def test_prompt_file_is_read_into_the_target(tmp_path):
    """CI keeps the prompt in the repo, so the diff that triggers the gate is
    the diff being evaluated."""
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Answer {{question}} briefly.", encoding="utf-8")
    body = cli.build_run_body(
        {"graders": ["valid_json"], "target": {"mode": "generate", "model": "groq/x", "prompt_file": str(prompt)}},
        "d",
        None,
    )
    assert body["target"]["prompt"] == "Answer {{question}} briefly."
    assert "prompt_file" not in body["target"]


def test_prompt_and_prompt_file_together_are_refused(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("x", encoding="utf-8")
    with pytest.raises(cli.CliError, match="use one"):
        cli.build_run_body(
            {"graders": ["valid_json"], "target": {"prompt": "y", "prompt_file": str(prompt)}}, "d", None
        )


def test_malformed_min_score_is_a_usage_error():
    with pytest.raises(cli.CliError, match="grader=value"):
        cli.parse_thresholds(["accuracy"])
    with pytest.raises(cli.CliError, match="isn't a number"):
        cli.parse_thresholds(["accuracy=high"])


def test_dataset_may_come_from_the_config(tmp_path):
    config = write_config(tmp_path, dataset=DATASET_ID)
    with respx.mock:
        respx.post(f"{BASE}/v1/eval-runs").mock(return_value=httpx.Response(201, json=run_row()))
        code, out = invoke(
            ["eval", "run", "--config", config, "--api-key", KEY, "--base-url", BASE]
        )
    assert code == cli.EXIT_OK, out


def test_the_api_key_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_API_KEY", KEY)
    monkeypatch.setenv("ANVIL_BASE_URL", BASE)
    with respx.mock:
        route = respx.post(f"{BASE}/v1/eval-runs").mock(
            return_value=httpx.Response(201, json=run_row())
        )
        code, _ = invoke(["eval", "run", "--dataset", DATASET_ID, "--config", write_config(tmp_path)])
    assert code == cli.EXIT_OK
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
