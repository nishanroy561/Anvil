# Anvil in five minutes

The path this walkthrough takes is the point of the product: **a user complains,
the complaint becomes a test case, and a change gets measured against it.** Every
step is a real command against a real stack — nothing is mocked and nothing is
skipped.

Prerequisites: Docker, and a clone of this repo. That is all.

---

## 0. Bring it up (about 90 seconds)

```bash
cp .env.example .env
docker compose up -d --build --wait
```

That is the whole install — database, queue, migrations, API, worker, dashboard
([ADR-0019](plan/adr/0019-fully-open-source-no-commercial-tiers.md): there is no
hosted tier, so this is the only path there is). `--wait` returns when every
container is *healthy*, not merely started.

```bash
docker compose exec api python -m anvil_api.seed
```

Prints a login, a project id, and an API key **shown once**. Copy the key.

Open <http://localhost:3000> and sign in with `demo@anvil.dev` / `demo1234`.

## 1. Make some traffic (about 60 seconds)

```bash
cd examples/demo-rag-app
pip install -e . -e ../../sdks/python
ANVIL_API_KEY=ak_… python -m demo seed --n 40
```

A toy RAG app instrumented with the SDK — about ten lines of integration, most of
it the `redact` hook. It produces a realistic mix: several models, ~8% errors, a
slow tail, varied sessions and users.

**Look at `/p/<project>/traces`.** Filter to `status=error`, open one, and read the
waterfall: nested spans, the retrieval step, the LLM call with its tokens and cost,
and the error with its stack. This is the debugging surface, and it is the reason
the SDK exists.

## 2. A user complains (about 30 seconds)

```bash
ANVIL_API_KEY=ak_… python -m demo serve --port 8100
```

Open <http://localhost:8100>, ask a question, and click **"No, that's wrong"**.
Type a reason and send.

One SDK call did that:

```python
anvil.feedback(trace_id, -1, comment="it said 5 days, mine took three weeks",
               user_ref=user.id)
```

The vote is safe to send the instant the user clicks — it routinely *overtakes*
its own trace, and Anvil holds it until the trace lands rather than rejecting the
race.

**Now open `/p/<project>/inbox`.** Your complaint is at the top, in the user's own
words. That is the failure inbox: downvoted traces that are not yet test cases.

## 3. Turn the complaint into a test case (about 20 seconds)

Tick it, click **Add to dataset**, create one called `user-reported`.

The row disappears — promoting is what clears the inbox, and the count going down
is the product working. The dataset item carries its **own copy** of the input and
outputs, so it will outlive the trace it came from (which matters in step 6).

Add a few more while you are here: go to `/traces`, filter `status=error`, select
several, and promote those too. Ten items is enough to measure something.

## 4. Score it (about 45 seconds)

`/p/<project>/evals` → **New run** → pick `user-reported` → mode **rescore** → pick
a grader or two.

Start with deterministic ones — `valid_json`, `contains`, `length_bounds` — which
are free and instant. The LLM judges (`accuracy`, `hallucination`, `tone`) cost
provider credits per item and say so in the form before you commit.

Watch it run, then read the results: per-grader means, pass rates, and
`errored_items` **shown separately rather than averaged in as zeros**. Expand a row
for the judge's reasoning.

> If a score comes back marked `tautological`, that is Anvil telling you the truth:
> a rescore run over freshly promoted items compares the recorded output against a
> copy of itself. Edit some expected outputs on the dataset page and rerun — now
> you are measuring something.
> ([ADR-0018](plan/adr/0018-recorded-output-is-kept-apart-from-expected-output.md))

## 5. Change something and prove it (about 60 seconds)

Make a second run over the same dataset — a different prompt, a different model,
different graders. Then select both on the runs list and click **Compare**.

The verdict is the part worth reading. Anvil will say `regressed`, `improved`, or
**`inconclusive`** — and it says inconclusive often, on purpose:

- fewer than 30 paired items,
- a 95% confidence interval that straddles zero,
- or scores that were tautological to begin with.

A tool that reports a 4% improvement from eight examples is worse than useless,
because you will act on it
([ADR-0008](plan/adr/0008-small-n-statistics.md)). Paired
bootstrap, and exact McNemar for binary graders.

## 6. Put it in CI (about 45 seconds)

```bash
pip install -e './sdks/python[cli]'
cd examples/ci-gate

# --dataset is the id from step 3 (the dashboard's dataset page shows it).
ANVIL_API_KEY=ak_… anvil eval run \
  --config eval.rescore.yaml \
  --dataset <dataset-id> \
  --wait \
  --min-score not_contains=0.8
echo "exit: $?"
```

```
run 269bb3eb… created (8 items)
  completed: 8/8

  grader          mean   pass   scored  errored
  length_bounds  0.750   0.75       8        0
  not_contains   1.000   1.00       8        0

PASSED
exit: 0
```

Then try `--min-score length_bounds=0.9` and watch it exit `1` with
`length_bounds: 0.750 < 0.900`.

`eval.rescore.yaml` is the credit-free variant used here. **`eval.yaml` next to it is
the one you would actually put in CI**: it uses `mode: generate`, because in CI the
prompt is the thing under test, and grading stored outputs would tell you nothing
about the diff. That one needs a provider key on the worker.

The exit codes are the product:

| | |
|---|---|
| `0` | thresholds met |
| `1` | a threshold failed, or a grader significantly regressed |
| `2` | the run never finished |
| `3` | bad config, credentials, or API |

`1` and `2` are deliberately different: "quality dropped" and "the eval never ran"
are different conversations, and a red check that conflates them gets ignored
within a week.

Three things also fail the gate that *look* like passes, and each would otherwise
go green while measuring nothing:

- a `--min-score` naming a grader absent from the run (try `not_containz=0.5` — a
  typo that silently disabled the check is the whole point),
- a grader where every item errored (`mean` is null, not zero),
- a grader whose scores are entirely tautological (ADR-0018).

A bad grader **config** is refused when the run is created rather than at the end —
try `min_chars` instead of `min` in the YAML and you get exit `3` naming the grader
and the offending keys, instead of a run that scores 1.0 on an empty config.

`workflow.yml` in that directory is a working GitHub Action.

---

## Worth seeing while you are here

**Monitors** (`/p/<project>/monitors`) — score live traffic on a schedule, so drift
shows up on a chart instead of in a support ticket. To watch one catch something:

```bash
cd examples/demo-rag-app && ANVIL_API_KEY=ak_… python -m demo seed --n 40 --drift
```

`--drift` degrades the answers **without raising the error rate**. The traces still
return `ok` and still cost money. Nothing but grading notices, which is the entire
argument for monitors.

**Usage** (`/p/<project>/settings`) — traces ingested and eval items graded per
month. No quota bar, no percentage, no upgrade prompt; there is nothing to be over.

**Anvil watching itself:**

```bash
docker compose --profile observability up -d --wait
```

Grafana on <http://localhost:3001>, Prometheus on <http://localhost:9090>. Set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` and one ingest request
becomes a single distributed trace across API → worker → Postgres in Tempo.

**Retention** — `RETENTION_DAYS=30` and the worker deletes traces older than that,
in batches. Unset is the default and means keep everything: an operator who has not
asked for deletion never gets it. Datasets are unaffected, which is why step 3
copied the payloads.

---

## If something goes wrong

| Symptom | Cause |
|---|---|
| `docker compose up` hangs on `--wait` | a container is unhealthy: `docker compose ps` then `docker compose logs <service>` |
| Traces accepted but never appear | the worker is down: `docker compose logs worker`. Ingest returns 202 the moment the trace is queued, by design |
| An eval run sits in `queued` | same — the worker owns the `eval:jobs` stream |
| Judge graders all error | no provider key. Name the model explicitly (`gemini-2.5-flash`); with none named the first key present wins, in a fixed order (ADR-0016) |
| The dashboard shows nothing | check the project switcher, and widen the time range — the default is 24h |
