# Evaluation System — Datasets, Graders, Comparison, CI, Monitoring, Feedback (Phases 5–9)

> Part of the [Anvil implementation plan](../plan.md). Schema in [architecture.md](architecture.md) §4; the grader machinery built here is reused by monitors (§5 below) and the CLI (§4). UI ground rules in [dashboard.md](dashboard.md).

## 1. Datasets (Phase 5)

- CRUD for datasets; `POST /v1/datasets/{id}/items` (manual add + bulk JSON upload); `POST /v1/datasets/{id}/promote` `{trace_ids: []}` — copies each trace's root input/output into items with `source_trace_id`. Wire the dashboard's "Add to dataset" button (single + bulk from the traces list).
- UI: dataset list, detail with items table, editable expected outputs.

## 2. Graders (Phase 5) — `apps/worker/anvil_worker/graders/`

All graders — built-in and third-party — register through the grader registry defined in [plugins.md](plugins.md) (`anvil.graders` contract); the UI's grader picker is registry-driven, so plugins appear automatically. Built-ins are just plugins that ship in-tree.

- **Deterministic:** `valid_json`, `contains`/`not_contains` (string or regex), `length_bounds` (chars or tokens), `exact_match`, `citation_present` (`[n]`-style or URL regex). Each returns score 0/1 + detail. Unit-test exhaustively.
- **LLM-as-judge** via LangChain with structured output (`{score: 0..1, reasoning: str}`): built-in prompts for `accuracy` (vs expected_output), `hallucination` (vs provided context/chunks), `tone`, plus `custom` (user rubric). Default judge model `claude-sonnet-5` via `ANTHROPIC_API_KEY`. Judge calls are themselves traced with the Anvil Python SDK into a designated internal project (dogfooding — great demo moment). No key configured → graceful `error: "no judge key configured"`.

## 3. Eval runs & the job queue (Phase 5)

This is the README's "job queue for long-running eval batches" challenge. Reuse Redis: `eval:jobs` stream + consumer group.

- `POST /v1/eval-runs` validates config, creates the row (`queued`), enqueues — with **spend guards**: 400 for runs over `MAX_EVAL_ITEMS` (default 500), 429 when the project already has `MAX_CONCURRENT_RUNS` (default 3) queued/running. Judge calls burn real credits; without caps a mistyped loop creates unbounded spend. ([pricing.md](pricing.md) layers a monthly eval-item quota on top.)
- `eval_runner.py`: claim job → `running` → per dataset item: produce target output (configured model/prompt via LangChain, `{{variables}}` filled from item input; or "re-score only" mode grading existing outputs) → run all graders (bounded concurrency ~5; per-item try/except) → write `eval_results` row → increment `progress`. On completion compute `summary` (per-grader mean/pass-rate, cost, latency). Cancellation: status check between items.
- **Crash recovery/resume:** runner heartbeats `eval_runs.updated_at` every ~15 s; at startup and periodically, reclaim runs with heartbeat stale > 2 min and **resume** — the unique `(run_id, item_id)` index makes per-item work idempotent (skip items that already have results).
- **Item snapshot:** at run start, freeze the dataset's current item IDs into `eval_runs.item_ids`; the run iterates the snapshot so mid-run dataset edits can't shift the ground. Comparison (§below) depends on this.
- **Provider rate limits:** target/judge calls retry on 429/overloaded with exponential backoff + jitter (cap ~60 s); after max retries the item records `error`, the run continues.
- **Grader-error aggregation rule:** an item whose grader errored is **excluded from that grader's mean/pass-rate** (never scored 0); `summary` reports `errored_items` per grader so degraded coverage is visible.
- Run APIs: list, get run + paginated results, `POST /v1/eval-runs/{id}/cancel`.
- UI: "New eval run" form (dataset → target: model + prompt textarea with `{{var}}` + params → graders with per-grader config); run detail with live progress (2 s poll), per-item results (input, output, scores, expandable judge reasoning), summary header.

**Tests:** graders exhaustively; e2e run with mocked LLMs over a 10-item dataset (statuses/progress/results/summary correct); cancellation; one item erroring doesn't fail the run; killing the runner mid-run and restarting resumes from the snapshot without duplicating results; grader-errored item excluded from that grader's aggregate.

**Acceptance (Phase 5):** in the UI — promote 10 demo traces → dataset → run eval with `valid_json` + judged `accuracy` → watch progress → per-item scores + summary.

## 4. Run comparison & statistics (Phase 6)

`anvil_api/services/analysis.py` (Pandas/NumPy) — lives in the **API**, not the worker: compare is computed synchronously by `GET /v1/eval-runs/compare?a=…&b=…`, and the API must not import from `apps/worker`. Runs must share a dataset.

- Join results on `item_id`, restricted to the **intersection of the two runs' item snapshots** (the dataset may have changed between runs). Response includes `compared_items` vs each run's totals; the UI must surface "compared on 8 of 12 items" whenever the intersection is smaller than either run. Per grader: mean A, mean B, delta — over pairs where both sides have a non-errored score.
- **Significance:** paired bootstrap on per-item deltas (10k resamples → 95% CI of mean delta) plus exact McNemar on discordant pairs for binary graders. Report `significant`, `ci: [lo, hi]`, `p_value`, `n`.
- **Honest-messaging rule:** `n < 30` or CI containing 0 → `verdict: "inconclusive"`; the UI says "not enough data to call it", never a bare percentage.
- Also compare cost and latency (mean + p95 + delta).
- UI `/evals/compare?a=&b=`: plain-language verdict banner ("v3 raised accuracy 71% → 84% [significant, CI +5..+21] but hallucination rose 3% → 9%"), per-grader table with deltas + CI, per-item diff (changed rows, both outputs side by side with judge reasoning). Entry points: run list (select two) and run page ("compare against…").
- Tests: hand-computed fixtures, including an n=5 inconclusive case and a clearly significant case.

**Acceptance (Phase 6):** comparing two seeded runs with a known shift shows correct deltas, CI, verdict; small-n shows "inconclusive".

## 5. CI gating (Phase 7)

- CLI in the Python SDK: `anvil eval run --dataset <id> --config eval.yaml --wait --min-score accuracy=0.8 --baseline-run <id>`. Creates a run via API, polls to completion, prints a results table, **exits non-zero** if a threshold fails or (with `--baseline-run`) any grader significantly regressed (uses the compare endpoint).
- `eval.yaml`: target (model, prompt file/inline, params) + graders — documented with an example.
- `examples/ci-gate/`: working GitHub Actions workflow — on PR touching `prompts/`, run the CLI, fail the check on regression.

**Acceptance (Phase 7):** CLI passes with a lenient threshold, exits 1 with a strict one; workflow YAML valid and documented.

## 6. Live monitoring, drift & alerts (Phase 8)

- **Monitors:** CRUD API + UI for `monitor_configs` (sample rate, graders, cron). `sampler.py` loop: every 30 s check due monitors (`croniter`, UTC); sample N recent unscored traces (Bernoulli by `sample_rate`, capped per run); grade root input/output via the §2–3 grader machinery (re-score mode); write `monitor_scores`. Monitor scoring counts toward the monthly eval-item quota ([pricing.md](pricing.md)).
- **Alerts:** `alerter.py` every 60 s: per enabled `alert_rule`, compute the metric over the trailing window (SQL for cost/latency/error-rate; `monitor_scores` mean for eval_score); fire `alert_events` on breach (no re-fire while unresolved), resolve when back under. Channels v1: dashboard bell + `projects.alert_webhook_url` (POST JSON; Pro+ only per pricing.md); log-only if unset. Channels resolve via the alert-channel registry ([plugins.md](plugins.md)) — the webhook is the built-in reference implementation; a failing channel never blocks the others.
- **UI:** `/monitors` — score-over-time chart per monitor (daily buckets), recent low-scoring samples linked to trace pages; alert rule CRUD + event history (fired/resolved).
- Demo seeder gains `--drift` mode emitting degraded traces so alerts demonstrably fire.
- Tests: sampler respects rate, never double-scores; alert fires on synthetic breach and resolves; webhook payload correct (respx).

**Acceptance (Phase 8):** with `--drift` data and a monitor + rule configured, the alert fires within one worker cycle, appears in the UI, webhook received.

## 7. Feedback loop (Phase 9)

- `POST /v1/feedback` (API-key auth): `{trace_id, rating, comment?, user_ref?}`. Mandatory edge handling:
  - **Feedback can race its trace** (thumbs-down lands while the trace is still in the Redis buffer): don't insert synchronously — return `202`, route through Redis stream `ingest:feedback` (consumed by the ingest worker); consumer retries rows whose trace doesn't exist yet with backoff, DLQ after 10 min. Tenant check at persist: trace must belong to the key's project, else drop + count.
  - **Repeat feedback upserts** on `(trace_id, user_ref)` (partial unique index) — toggling 👎→👍 never creates two rows.
  - **Rate limit** per API key (~600/min): reachable from customers' end users — the most abuse-exposed surface after ingest.
- SDKs: `client.feedback(trace_id, rating, comment)` in both; document round-tripping `trace.id` to the frontend.
- UI: feedback badge + comment on trace header; `feedback=negative` list filter; bulk-select negatives → Add to dataset; **Failure inbox** — recent thumbs-down traces not yet in any dataset (`LEFT JOIN dataset_items ON source_trace_id`).
- Demo app gets a thumbs-down button.
- Tests: feedback attaches + tenant-checked; inbox query correct; feedback submitted before its trace persists attaches once it lands; repeat feedback from one user_ref upserts.

**Acceptance (Phase 9):** thumbs-down in demo app → Failure inbox → promote to dataset → next eval run includes it.
