# ADR-0018 — The recorded output is kept apart from the expected output

Status: **Accepted** — 2026-07-28

## Context

Promoting a trace into a dataset copied the trace's root output into
`dataset_items.expected_output` and nowhere else, because `dataset_items` has
columns for `input`, `expected_output`, `metadata` and `source_trace_id` — and
none for the output the application actually produced.

An eval run in `mode="rescore"` then graded `item["expected_output"]`. So the
value being graded and the reference it was graded against were the same value.

Two graders score an output *against* the reference: `exact_match`
(`deterministic.py`) and the `accuracy` judge (`judges.py`). Both are
tautological under those conditions — `exact_match` compares a string with
itself and returns 1.0; `accuracy` asks a model "does this answer match this
identical answer" and is told 1.0. The remaining graders (`valid_json`,
`length_bounds`, `contains`, `not_contains`, `citation_present`, `tone`,
`hallucination`) judge the output on its own terms or against retrieved chunks,
and were never affected.

This explains every `mean 1.0` in the Phase 5 acceptance runs. STATUS had
recorded it as a caveat about how to read those numbers; it is not a reading
problem, it is a missing place to put the recorded output.

It also blocks Phase 6. Comparison is built on the premise that a delta means
something, and its headline feature is refusing to call a result that the data
does not support. A comparison of two rescore runs returns delta 0.0 with a
clean confidence interval, which reads as "no regression" when the truth is
"this measured nothing" — the exact dishonesty the phase exists to prevent.

## Decision

**Promotion stores the trace's output in `metadata.recorded_output`, and
rescore grades that.** `expected_output` keeps the same value at promotion time
but is now solely the reference — the field a human edits to say what the
answer *should* have been.

Supporting pieces:

- Anvil's own metadata keys are written *after* the customer's are spread in, so
  a trace carrying `recorded_output` in its own metadata cannot shadow the real
  one. (The previous ordering let customer metadata shadow
  `promoted_from_trace` too.)
- `PATCH /v1/dataset-items/{id}` sets `metadata.expected_output_edited = true`
  when `expected_output` changes, recording that the reference is now a genuine
  reference.
- Graders declare `compares_expected: bool`, alongside the existing `kind`.
  Only `exact_match` and `accuracy` set it. Plugins that omit it default to
  False — a missed flag costs a warning nobody sees, a false one would cry wolf
  on honest scores.
- When the graded output equals the expected output, any score from a
  `compares_expected` grader is marked `tautological: true` on the score entry,
  and `summarize()` reports `tautological_items` per grader.

### No migration

`dataset_items.metadata` is already JSONB and already carries Anvil-authored
keys (`promoted_from_trace`, `trace_name`). `plan.md` states migration `0001`
is the complete v1 schema and later phases should not need migrations, so a
`0002` adding a column is a deviation this problem does not justify.

The trade-off is real and accepted: `recorded_output` is unindexed and
unconstrained, and nothing at the database level stops it being absent or the
wrong shape. Both consumers treat it as optional and fall back, so the cost is
weaker guarantees rather than breakage. If a later phase needs to query or
constrain it, promoting it to a column is a contained change.

### Detection is by value, not by flag

`expected_output_edited` records intent, but the tautology check compares the
two values directly. Items promoted before this ADR have no
`recorded_output` and no flag, and a human can edit an expected output back to
the recorded value. Comparing values catches every such case; trusting the flag
would miss them.

### Tautological scores are counted, not excluded

Unlike a grader error, a tautological score is a real score — it is simply
uninformative. Excluding it would shrink the run silently. Counting it lets the
reader discount the mean themselves, and gives Phase 6 something concrete to
refuse to compare on.

## Consequences

- Rescore runs over promoted items now measure the application's real output.
  A `1.0` from `exact_match` means the output matched a reference someone wrote,
  not that a string equals itself.
- **Existing promoted items are not retroactively fixed.** They have no
  `recorded_output`, so they keep the old fallback behaviour and their
  reference-comparing scores come back marked `tautological`. Re-promote them,
  or backfill from `source_trace_id`, which survives trace deletion as
  `ON DELETE SET NULL`.
- Rescore is now only as good as the references in the dataset. That is the
  honest position: a rescore run over entirely unedited items is legitimately
  uninformative for `exact_match` and `accuracy`, and now says so.
- Phase 6 gets a signal it can act on. A comparison whose graders are largely
  tautological should decline to render a verdict rather than report a
  confident zero delta.

## Alternatives rejected

**Add a `recorded_output` column (migration `0002`).** Semantically cleaner —
it is a first-class field, not an annotation. Rejected on the settled
constraint that `0001` is the whole v1 schema. Reconsider if a later phase
needs to query it.

**Leave it, and rely on humans editing expected outputs.** The UI already
supports editing them, so the intended workflow was promote → edit → rescore.
But an unedited item scored 1.0 with no indication anything was wrong, and
"the user was supposed to know" is not a property of an honest tool.

**Drop rescore mode.** It is genuinely useful for the seven graders that judge
output in isolation — checking a corpus of real outputs for malformed JSON or
missing citations needs no reference at all.
