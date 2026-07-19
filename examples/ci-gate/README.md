# CI Gate Example (Phase 7 — not built yet)

Spec: `plan/evaluation.md` §5. GitHub Actions workflow that runs
`anvil eval run … --wait --min-score …` on PRs touching `prompts/` and fails
the check on a significant regression vs the baseline run.
