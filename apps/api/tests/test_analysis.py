"""Comparison statistics (plan/evaluation.md §4, ADR-0008).

No services needed — `analysis.py` is pure functions over dicts.

The tests that matter here are the ones about *refusing* to call a result: a
statistics module that only proves it can find an effect is half-tested, and
the half it skipped is the half this product exists for.
"""

import uuid

import pytest

from anvil_api.services.analysis import (
    MIN_N,
    _mcnemar_exact,
    compare_grader,
    compare_runs,
)


def pairs(values, tautological=False):
    """(a, b) tuples → the (a, b, tautological) triples compare_grader takes."""
    return [(float(a), float(b), tautological) for a, b in values]


# ---- the honesty rule -------------------------------------------------------


def test_small_n_is_inconclusive_however_large_the_gap():
    """n=5 with every item improving 0 → 1. The delta could not be more
    obvious, and it is still not enough data to call it (ADR-0008)."""
    result = compare_grader("accuracy", pairs([(0, 1)] * 5))

    assert result["delta"] == 1.0
    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "insufficient_data"
    assert result["significant"] is False


def test_a_clear_shift_at_adequate_n_is_called():
    """40 items, every one improving. Nothing subtle — if this reads
    inconclusive the module is broken in the other direction."""
    result = compare_grader("accuracy", pairs([(0, 1)] * 40))

    assert result["verdict"] == "improved"
    assert result["significant"] is True
    assert result["delta"] == 1.0
    lo, hi = result["ci"]
    assert lo > 0


def test_a_clear_regression_is_called_a_regression():
    result = compare_grader("accuracy", pairs([(1, 0)] * 40))

    assert result["verdict"] == "regressed"
    assert result["significant"] is True
    assert result["delta"] == -1.0
    assert result["ci"][1] < 0


def test_noise_at_adequate_n_is_inconclusive():
    """40 items split evenly between improving and regressing: mean delta 0.
    The CI must straddle zero and the verdict must decline."""
    result = compare_grader("accuracy", pairs([(0, 1)] * 20 + [(1, 0)] * 20))

    assert result["delta"] == 0.0
    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "ci_contains_zero"
    lo, hi = result["ci"]
    assert lo <= 0 <= hi


def test_identical_runs_are_inconclusive_not_a_pass():
    """Comparing a run against itself is delta 0 with a tight CI. It must never
    read as 'no regression, confirmed' — that is a claim about the change, and
    there was no change."""
    result = compare_grader("accuracy", pairs([(1, 1)] * 40))

    assert result["delta"] == 0.0
    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "ci_contains_zero"


def test_min_n_boundary():
    """The rule is n < 30, so 29 declines and 30 is allowed to speak."""
    assert compare_grader("g", pairs([(0, 1)] * (MIN_N - 1)))["reason"] == "insufficient_data"
    assert compare_grader("g", pairs([(0, 1)] * MIN_N))["verdict"] == "improved"


# ---- tautological scores (ADR-0018) ----------------------------------------


def test_all_tautological_pairs_refuse_to_compare():
    """Both sides graded against their own output. A zero delta here would
    report 'no regression' when the truth is that nothing was measured."""
    result = compare_grader("exact_match", pairs([(1, 1)] * 40, tautological=True))

    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "tautological"
    assert result["tautological_pairs"] == 40
    # Refused before the bootstrap ran — there is nothing to put an interval on.
    assert result["ci"] is None


def test_some_tautological_pairs_are_counted_but_still_compared():
    """A partly-tautological grader is degraded, not void. The count is
    surfaced so the reader can discount it."""
    result = compare_grader(
        "exact_match", pairs([(0, 1)] * 35) + pairs([(1, 1)] * 5, tautological=True)
    )

    assert result["tautological_pairs"] == 5
    assert result["n"] == 40
    assert result["verdict"] == "improved"


# ---- McNemar ----------------------------------------------------------------


def test_mcnemar_matches_hand_computed_values():
    """Two-sided exact binomial over discordant pairs.

    n=10, k=1: 2 * (C(10,0) + C(10,1)) / 2^10 = 2 * 11 / 1024.
    """
    assert _mcnemar_exact(9, 1) == pytest.approx(2 * 11 / 1024)
    # No discordant pairs: nothing distinguishes the runs.
    assert _mcnemar_exact(0, 0) == 1.0
    # A perfectly even split is as unsurprising as it gets — p clamps at 1.0.
    assert _mcnemar_exact(5, 5) == 1.0


def test_mcnemar_only_runs_for_binary_graders():
    """Continuous scores get a CI but no McNemar p-value — the test is defined
    on discordant pairs, which graded 0.4 vs 0.7 does not have."""
    binary = compare_grader("g", pairs([(0, 1)] * 20 + [(1, 1)] * 20))
    continuous = compare_grader("g", pairs([(0.4, 0.7)] * 40))

    assert binary["p_value"] is not None
    assert continuous["p_value"] is None
    assert continuous["ci"] is not None


def test_mcnemar_is_exact_not_approximated():
    """Chi-squared would overstate significance at these counts; the exact test
    is the reason this is not a one-liner. 3 discordant pairs, all one way:
    2 * C(3,0) / 2^3 = 0.25 — nowhere near significant, as it should be."""
    assert _mcnemar_exact(0, 3) == pytest.approx(0.25)


# ---- pairing and intersection ----------------------------------------------


def _run(run_id, item_ids, graders=("accuracy",), name="run"):
    return {
        "id": run_id,
        "name": name,
        "item_ids": [str(i) for i in item_ids],
        "grader_config": {"graders": [{"name": g, "config": {}} for g in graders]},
    }


def _result(item_id, score, **kw):
    row = {
        "item_id": item_id,
        "scores": {"accuracy": {"score": score}} if score is not None else {},
        "latency_ms": kw.get("latency_ms", 100),
        "cost_usd": kw.get("cost_usd", 0.001),
    }
    row["scores"].update(kw.get("extra_scores", {}))
    return row


def test_comparison_is_restricted_to_the_snapshot_intersection():
    """The dataset grew between runs. Only the shared items are comparable, and
    the response has to say so or a partial comparison passes for a whole one."""
    shared = [uuid.uuid4() for _ in range(8)]
    only_b = [uuid.uuid4() for _ in range(4)]

    out = compare_runs(
        _run("a", shared),
        _run("b", shared + only_b),
        [_result(i, 0.0) for i in shared],
        [_result(i, 1.0) for i in shared + only_b],
    )

    assert out["compared_items"] == 8
    assert out["partial"] is True
    assert out["run_a"]["total_items"] == 8
    assert out["run_b"]["total_items"] == 12
    assert out["graders"][0]["n"] == 8


def test_identical_snapshots_are_not_partial():
    items = [uuid.uuid4() for _ in range(5)]
    out = compare_runs(
        _run("a", items),
        _run("b", items),
        [_result(i, 1.0) for i in items],
        [_result(i, 1.0) for i in items],
    )
    assert out["partial"] is False
    assert out["compared_items"] == 5


def test_disjoint_snapshots_compare_nothing_rather_than_erroring():
    out = compare_runs(
        _run("a", [uuid.uuid4() for _ in range(3)]),
        _run("b", [uuid.uuid4() for _ in range(3)]),
        [],
        [],
    )
    assert out["compared_items"] == 0
    assert out["graders"][0]["verdict"] == "inconclusive"
    assert out["graders"][0]["reason"] == "no_paired_scores"


def test_a_grader_error_on_either_side_drops_the_pair():
    """An errored grader is an absence, not a zero (plan/evaluation.md §3).
    Scoring it 0 would invent a regression out of a provider timeout."""
    items = [uuid.uuid4() for _ in range(4)]
    results_a = [_result(i, 1.0) for i in items]
    results_b = [_result(i, 1.0) for i in items]
    results_b[0]["scores"]["accuracy"] = {"error": "grader timed out after 30s"}

    out = compare_runs(_run("a", items), _run("b", items), results_a, results_b)

    grader = out["graders"][0]
    assert grader["n"] == 3, "errored pair was counted instead of dropped"
    assert grader["mean_b"] == 1.0, "error was averaged in as a zero"


def test_graders_present_in_only_one_run_still_appear():
    """Run B added a grader. It cannot be compared, but silently dropping it
    would hide that B measured something A did not."""
    items = [uuid.uuid4() for _ in range(4)]
    out = compare_runs(
        _run("a", items, graders=("accuracy",)),
        _run("b", items, graders=("accuracy", "tone")),
        [_result(i, 1.0) for i in items],
        [_result(i, 1.0) for i in items],
    )

    names = [g["grader"] for g in out["graders"]]
    assert names == ["accuracy", "tone"]
    tone = next(g for g in out["graders"] if g["grader"] == "tone")
    assert tone["n"] == 0
    assert tone["reason"] == "no_paired_scores"


def test_latency_and_cost_are_compared_over_the_intersection():
    items = [uuid.uuid4() for _ in range(4)]
    out = compare_runs(
        _run("a", items),
        _run("b", items),
        [_result(i, 1.0, latency_ms=100, cost_usd=0.001) for i in items],
        [_result(i, 1.0, latency_ms=200, cost_usd=0.002) for i in items],
    )

    assert out["latency"]["a"] == 100
    assert out["latency"]["b"] == 200
    assert out["latency"]["delta"] == 100
    assert out["cost"]["a"] == pytest.approx(0.004)
    assert out["cost"]["b"] == pytest.approx(0.008)
    assert out["cost"]["delta"] == pytest.approx(0.004)


# ---- reproducibility --------------------------------------------------------


def test_the_interval_is_stable_across_calls():
    """A user refreshing the page must not watch the CI move. Without a fixed
    seed the bootstrap jitters, which reads as instability in the measurement."""
    data = pairs([(0, 1)] * 20 + [(1, 0)] * 10 + [(0.5, 0.5)] * 10)
    first = compare_grader("g", data)["ci"]
    second = compare_grader("g", data)["ci"]
    assert first == second
