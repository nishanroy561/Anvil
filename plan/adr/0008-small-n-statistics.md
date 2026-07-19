# ADR-0008: Paired bootstrap + McNemar with mandatory "inconclusive" verdict

Status: Accepted · Date: 2026-07 · Spec: ../evaluation.md §4

## Context
Eval datasets are small (often 10–50 items). Comparing two runs by bare mean deltas ("71% → 84%!") on n=12 is how prompt changes ship on noise — the exact failure the product exists to prevent.

## Decision
Paired analysis on the item-snapshot intersection: bootstrap CI (10k resamples) on per-item deltas, exact McNemar for binary graders. When n < 30 or the CI contains 0, the API returns `verdict: "inconclusive"` and the UI must say so — never a bare percentage.

## Consequences
Anvil sometimes tells users "not enough data to call it," which is less satisfying and more trustworthy than false confidence — that honesty is a product feature. Pairing requires the item snapshot (`eval_runs.item_ids`) and intersection semantics with "compared on n of m" surfaced.

## Alternatives considered
- **t-test on means** — wrong for paired 0/1 scores at small n.
- **Just show deltas** — the industry default and the thing being explicitly rejected.
