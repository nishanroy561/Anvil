"""Paired run comparison (plan/evaluation.md §4, ADR-0008).

Lives in the API, not the worker: compare is computed synchronously on request
and the API must not import from `apps/worker` (ADR-0014).

The whole point of this module is refusing to overclaim. Eval datasets are
small — 10 to 50 items is normal — and a bare mean delta on n=12 is how prompt
changes ship on noise. So every comparison carries a confidence interval, and
anything the data cannot support comes back `inconclusive` with a reason rather
than a number someone will screenshot.

Pure functions over plain dicts: no ORM, no I/O. The router loads rows and hands
them here, which is what makes the hand-computed fixtures in the tests possible.
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Iterable

import numpy as np

# ADR-0008 fixes these. They are not tuning knobs — moving them changes what
# the product claims, so a change here needs a superseding ADR.
BOOTSTRAP_RESAMPLES = 10_000
MIN_N = 30
CI_PERCENTILES = (2.5, 97.5)

# A fixed seed, so the same comparison returns the same interval every time it
# is requested. Without it a user refreshing the page watches the CI jitter,
# which reads as instability in the measurement rather than in the estimator.
BOOTSTRAP_SEED = 0

Verdict = str  # "improved" | "regressed" | "inconclusive"


def _usable(entry: Any) -> bool:
    """A score that can take part in a pair.

    A grader error is not a zero — it is an absence (plan/evaluation.md §3), so
    the pair is dropped rather than counted against the run.
    """
    return isinstance(entry, dict) and "error" not in entry and entry.get("score") is not None


def _mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value over discordant pairs.

    Exact rather than chi-squared: at these sample sizes the discordant count is
    routinely under 10, where the chi-squared approximation is unreliable in
    precisely the direction that manufactures significance.
    """
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * tail / (2**n))


def _bootstrap_ci(deltas: np.ndarray) -> tuple[float, float]:
    """Percentile CI of the mean paired delta, over `BOOTSTRAP_RESAMPLES`."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, deltas.size, size=(BOOTSTRAP_RESAMPLES, deltas.size))
    means = deltas[idx].mean(axis=1)
    lo, hi = np.percentile(means, CI_PERCENTILES)
    return float(lo), float(hi)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), pct))


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def compare_grader(
    grader: str,
    pairs: list[tuple[float, float, bool]],
) -> dict:
    """Statistics for one grader over its paired scores.

    `pairs` is (score_a, score_b, tautological) in a stable order — the caller
    sorts, because the bootstrap must be reproducible.
    """
    n = len(pairs)
    tautological = sum(1 for *_, taut in pairs if taut)
    scores_a = [a for a, _, _ in pairs]
    scores_b = [b for _, b, _ in pairs]

    result: dict[str, Any] = {
        "grader": grader,
        "n": n,
        "mean_a": _mean(scores_a),
        "mean_b": _mean(scores_b),
        "delta": _delta(_mean(scores_a), _mean(scores_b)),
        "ci": None,
        "p_value": None,
        "significant": False,
        "verdict": "inconclusive",
        "reason": None,
        "tautological_pairs": tautological,
    }

    if n == 0:
        result["reason"] = "no_paired_scores"
        return result

    # Both sides were graded against their own output, so neither number
    # measured anything and the delta between them is doubly meaningless
    # (ADR-0018). This is the case a zero delta would otherwise report as
    # "no regression" — the exact false confidence this module exists to refuse.
    if tautological == n:
        result["reason"] = "tautological"
        return result

    deltas = np.asarray([b - a for a, b, _ in pairs], dtype=float)
    lo, hi = _bootstrap_ci(deltas)
    result["ci"] = [lo, hi]

    # Binary is decided from the data, so a plugin grader that happens to emit
    # 0/1 gets the exact test without having to declare anything.
    if all(s in (0.0, 1.0) for s in scores_a + scores_b):
        only_a = sum(1 for a, b, _ in pairs if a == 1.0 and b == 0.0)
        only_b = sum(1 for a, b, _ in pairs if a == 0.0 and b == 1.0)
        result["p_value"] = _mcnemar_exact(only_a, only_b)

    ci_spans_zero = lo <= 0.0 <= hi
    if n < MIN_N:
        result["reason"] = "insufficient_data"
    elif ci_spans_zero:
        result["reason"] = "ci_contains_zero"
    else:
        result["significant"] = True
        result["verdict"] = "improved" if result["delta"] > 0 else "regressed"

    return result


def compare_runs(
    run_a: dict,
    run_b: dict,
    results_a: Iterable[dict],
    results_b: Iterable[dict],
) -> dict:
    """Compares two runs over the intersection of their item snapshots.

    The dataset can change between runs — items added, items deleted — so the
    snapshots (`eval_runs.item_ids`, frozen at claim) are what make the
    comparison paired at all. Anything outside the intersection is not
    comparable and is excluded, with `compared_items` vs each run's total left
    visible so a shrunken comparison cannot pass for a whole one.
    """
    ids_a = {uuid.UUID(str(i)) for i in (run_a.get("item_ids") or [])}
    ids_b = {uuid.UUID(str(i)) for i in (run_b.get("item_ids") or [])}
    # Sorted, so the bootstrap resamples a stable ordering.
    intersection = sorted(ids_a & ids_b)

    by_item_a = {r["item_id"]: r for r in results_a}
    by_item_b = {r["item_id"]: r for r in results_b}

    graders: list[str] = []
    for run in (run_a, run_b):
        for spec in ((run.get("grader_config") or {}).get("graders") or []):
            name = spec.get("name")
            if name and name not in graders:
                graders.append(name)

    per_grader = []
    for grader in graders:
        pairs: list[tuple[float, float, bool]] = []
        for item_id in intersection:
            ra, rb = by_item_a.get(item_id), by_item_b.get(item_id)
            if ra is None or rb is None:
                continue
            ea = (ra.get("scores") or {}).get(grader)
            eb = (rb.get("scores") or {}).get(grader)
            if not _usable(ea) or not _usable(eb):
                continue
            pairs.append(
                (
                    float(ea["score"]),
                    float(eb["score"]),
                    bool(ea.get("tautological") or eb.get("tautological")),
                )
            )
        per_grader.append(compare_grader(grader, pairs))

    def _run_metrics(by_item: dict) -> dict:
        rows = [by_item[i] for i in intersection if i in by_item]
        latencies = [float(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None]
        costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None]
        return {
            "mean_latency_ms": _mean(latencies),
            "p95_latency_ms": _percentile(latencies, 95),
            "total_cost_usd": sum(costs) if costs else 0.0,
            "mean_cost_usd": _mean(costs),
        }

    metrics_a, metrics_b = _run_metrics(by_item_a), _run_metrics(by_item_b)

    return {
        "run_a": {"id": str(run_a["id"]), "name": run_a.get("name"), "total_items": len(ids_a)},
        "run_b": {"id": str(run_b["id"]), "name": run_b.get("name"), "total_items": len(ids_b)},
        "compared_items": len(intersection),
        # True whenever the pairing lost items — the UI must say "compared on
        # n of m" rather than presenting a partial comparison as complete.
        "partial": len(intersection) < max(len(ids_a), len(ids_b)),
        "graders": per_grader,
        "latency": {
            "a": metrics_a["mean_latency_ms"],
            "b": metrics_b["mean_latency_ms"],
            "delta": _delta(metrics_a["mean_latency_ms"], metrics_b["mean_latency_ms"]),
            "p95_a": metrics_a["p95_latency_ms"],
            "p95_b": metrics_b["p95_latency_ms"],
            "p95_delta": _delta(metrics_a["p95_latency_ms"], metrics_b["p95_latency_ms"]),
        },
        "cost": {
            "a": metrics_a["total_cost_usd"],
            "b": metrics_b["total_cost_usd"],
            "delta": _delta(metrics_a["total_cost_usd"], metrics_b["total_cost_usd"]),
            "mean_a": metrics_a["mean_cost_usd"],
            "mean_b": metrics_b["mean_cost_usd"],
        },
    }
