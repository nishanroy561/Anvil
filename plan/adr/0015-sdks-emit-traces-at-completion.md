# ADR-0015: SDKs emit a trace once, at completion

Status: Accepted · Date: 2026-07 · Spec: ../sdk.md §1

## Context
The wire format has a `status: "in_progress"` value, the consumer carries a monotonic completion guard so a late `in_progress` can never overwrite a finished trace (ADR-0003), and the worker runs a reaper that marks traces `abandoned` when they sit `in_progress` for 30 minutes. That machinery invites an obvious reading: the SDK should announce a trace when it starts and update it when it ends, so long-running work is visible while it is still running.

Doing that doubles ingest volume for every traced request, and the second write exists only to correct the first. It also puts a network send on the *entry* to a customer's request path rather than the exit, which is where ADR-0013 least wants it.

## Decision
The SDKs queue a trace exactly once, when the root trace ends. Spans are carried inside that single payload rather than shipped individually. Nothing emits `in_progress` from the client.

`in_progress` remains a valid wire status, and the guards and reaper stay: they defend against out-of-order delivery within a batch, retried batches, and future producers (the eval runner, a customer posting to `/v1/ingest/traces` directly) that legitimately do report partial state.

## Consequences
One write per trace, and no in-flight state to reconcile. A trace becomes visible only after the request that produced it finishes — for a 30-second RAG pipeline the dashboard shows nothing for 30 seconds, which is acceptable for an observability tool and is the trade every batching tracer makes.

Because clients never emit `in_progress`, the reaper's `abandoned` sweep will find nothing from SDK traffic in practice — it is defence for other producers, not dead code, and its test coverage should not be read as covering an SDK path. A crashed customer process loses its queued traces rather than leaving them `in_progress` forever; ADR-0013 already accepts that loss.

If streaming/agent runs later need live partial traces, that is a new decision superseding this one — likely a `trace.checkpoint()` call that opts in per trace rather than a change to the default.

## Alternatives considered
- **Emit `in_progress` on trace start, update on end** — doubles ingest volume and moves a send to the front of the request path, to fix visibility during a window measured in seconds.
- **Ship each span as it closes** — one request per span; the trace tree would then have to be assembled from partial arrivals on every read.
- **Emit `in_progress` only past a duration threshold** — a timer per open trace inside the customer's process, plus two code paths to keep correct, for a case v1 has no user for.
