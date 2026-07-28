# CI gate

Fail a pull request when a prompt change makes the output worse.

`anvil eval run` creates an eval run against a dataset, waits for it, prints the
per-grader results, and exits non-zero if a score fell below a threshold or a
grader **significantly** regressed against a baseline run.

## The 60-second version

```bash
pip install 'anvil-sdk[cli]'          # in-tree: pip install './sdks/python[cli]'
export ANVIL_API_KEY=ak_...           # Settings -> API keys in the dashboard
export ANVIL_BASE_URL=https://anvil.internal   # default: http://localhost:8000

anvil eval run \
  --dataset <dataset-id> \
  --config eval.yaml \
  --wait \
  --min-score accuracy=0.8 \
  --baseline-run <previous-run-id>
```

```
run 9f3c… created (40 items)
  running: 12/40
  completed: 40/40

  grader             mean   pass   scored  errored
  accuracy          0.910   1.00       40        0
  citation_present  0.975   0.98       40        0

  cost $0.014820

  compared on 40 items
  accuracy: improved 0.840 → 0.910
  citation_present: inconclusive (ci_contains_zero) 0.980 → 0.975

PASSED
```

## Files

| File | What it is |
|---|---|
| `eval.yaml` | The eval config — target model, prompt, graders. Commented. |
| `workflow.yml` | GitHub Actions workflow. Copy to `.github/workflows/eval-gate.yml`. |

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | Thresholds met, no significant regression | Merge |
| `1` | A threshold failed, or a grader significantly regressed | Look at the change |
| `2` | The run failed, was cancelled, or timed out | Look at the worker |
| `3` | Bad config, bad credentials, API unreachable | Look at the setup |

**1 and 2 are deliberately different.** "Quality dropped" is a verdict about
your change; "the eval never ran" is an infrastructure problem. A red check that
conflates them is a red check people learn to ignore.

## What the gate will and won't fail on

It fails on a **significant** regression only. Anvil compares two runs with a
paired bootstrap and reports `inconclusive` when the data can't support a call —
n < 30, or a confidence interval that still contains zero
([ADR-0008](../../plan/adr/0008-small-n-statistics.md)). Those don't block the
merge, because a gate that fires on noise gets switched off within a week.

The practical consequence: **a 12-item dataset will almost never block
anything.** If you want the gate to have teeth, grow the dataset. The failure
inbox (Phase 9) is designed to feed it from real thumbs-down traffic.

Three things *will* fail the gate that might look like passes:

- A grader named in `--min-score` that isn't in the run — a typo'd name would
  otherwise silently disable that check while the build stays green.
- A grader where every item errored. `mean` is null, not zero; nothing was
  measured, so it can't have passed.
- A grader where every score was tautological — the output was graded against
  itself, so a perfect score was the only possible outcome
  ([ADR-0018](../../plan/adr/0018-recorded-output-is-kept-apart-from-expected-output.md)).
  This happens with `mode: rescore` over promoted items whose expected outputs
  were never edited.

## Credentials

CI holds **`ANVIL_API_KEY`**, a project-scoped key
([ADR-0021](../../plan/adr/0021-eval-apis-accept-either-a-session-or-an-api-key.md)) —
never a user's email and password. It reaches exactly one project, rotates
independently, and revoking it locks nobody out of the dashboard.

**Provider keys (Anthropic / Google / Groq) do not go in CI.** They live on the
Anvil worker, which is what actually calls the models. CI only ever talks to
the Anvil API.

## Picking a baseline

`--baseline-run` is the run the new one is compared against. Two common
patterns:

- **A pinned golden run** — store its id in a repository variable
  (`vars.ANVIL_BASELINE_RUN`, as the workflow does) and bump it deliberately
  when you accept a new normal. Stable, and drift is a decision.
- **The last run on the default branch** — fetch it with
  `GET /v1/projects/{id}/eval-runs?dataset_id=…`. Always current, but a slow
  slide downward can pass every individual comparison.

Pin one unless you have a reason not to.
