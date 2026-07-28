# ADR-0017: Judge calls are traced into a designated internal project

Status: Accepted · Date: 2026-07 · Spec: ../evaluation.md §2

## Context
[evaluation.md](../evaluation.md) §2 asks for judge calls to be "traced with the Anvil Python SDK into a designated internal project (dogfooding — great demo moment)". Phase 5 shipped without it, deliberately: the worker had no dependency on `anvil-sdk` and there was no notion of an internal project, so the choice was between deferring it and half-building it.

The value is not only the demo. A judge is an LLM call whose output silently becomes a number in a customer-facing report. When a score looks wrong, the question is always "what prompt produced that?" — and without tracing, the answer lives in a log line that was never written. Anvil exists to answer exactly that question for other people's LLM calls; declining to answer it for its own is a poor argument for the product.

Three design questions had to be settled: where the credential comes from, which client ships the traces, and what happens when any of it fails.

## Decision
**Judge tracing is opt-in via `ANVIL_INTERNAL_API_KEY`**, an ordinary Anvil API key naming the project that receives judge traces (`ANVIL_INTERNAL_BASE_URL` aims it, defaulting to `http://localhost:8000`). Unset is the default and does nothing. Resolution lives in `anvil_worker/dogfood.py`; `judges.py` wraps every judge call in `trace_judge(...)` and calls `recorder.record(...)` with the verdict.

Keys are read from the process environment after `providers.load_env()`, for the reason ADR-0016 already gives: worker settings resolve `.env` against the working directory, which differs between dev and the image.

**A dedicated `AnvilClient`, not the global `anvil.init()` singleton.** The client is passed explicitly to the trace. The worker is not an instrumented application, and a global client would make any incidental `anvil.span()` anywhere in the worker start shipping to the customer's internal project.

**Shape:** one trace per judge call, `judge:<grader>`, containing one `llm` span named after the model. Tokens live on spans in the wire format, so the two levels are what make judge spend visible; the flat alternative reports cost of nothing. The trace wraps the *whole* call including retries — a judge that succeeded on its third attempt is precisely what is worth being able to see.

**Every failure degrades to a no-op.** Missing SDK, client construction raising, the SDK raising, a dead API: each yields an inert recorder and judging proceeds unchanged. ADR-0013 already forbids the SDK from raising into caller code; this module holds the same line over the setup ADR-0013 does not cover.

## Consequences
A judged eval run is now observable as a waterfall like any other instrumented app — prompt, verdict, tokens, cost, latency, and provider errors. Verified live: a 10-item judged run produced 10 `judge:accuracy` traces whose statuses matched the run exactly (6 ok, 4 errored on a provider 429), with the provider's own error text on the errored traces and cost rolled up on the successful ones.

This creates the project's first **worker → API dependency**. It is HTTP over the public ingest endpoint with an ordinary API key — the worker gains no privileged path and no import of API code (ADR-0014 holds) — and it is inert unless configured. The cost is that the worker can now generate ingest traffic, so a misconfiguration is self-inflicted load rather than a crash.

**The internal project must be one created for the purpose.** Judge traces arrive through normal ingest, so aiming the key at a project under evaluation mixes Anvil's own traffic into the customer data being measured — and, since traces can be promoted into datasets, potentially into a future eval. That is a documentation and defaults problem, not something the code can detect: an API key does not announce what its project is for.

`anvil-sdk` is imported lazily and is not declared as a worker dependency, because it is not published — it installs from `sdks/python` in-tree. A set key with no SDK installed logs a warning rather than failing silently; the operator asked for the feature, so silence would read as breakage.

## Alternatives considered
- **A span on the eval run's own trace instead of a standalone trace** — the eval runner does not produce traces of its own, so there is nothing to hang it on; inventing one would mean tracing the runner, a much larger change.
- **Global `anvil.init()` in the worker entrypoint** — simpler by a few lines, but silently widens what gets captured to anything in the worker that ever calls the SDK.
- **A dedicated `judge_traces` table written directly by the worker** — no SDK, no HTTP hop, but it builds a second, private observability system inside a product whose entire thesis is that its own one is good enough. It would also miss the demo moment and diverge from the customer-facing trace format immediately.
- **Always-on, defaulting to the project being evaluated** — no configuration, but it pollutes measured data with Anvil's own traffic and gives self-hosters telemetry they never asked for.
