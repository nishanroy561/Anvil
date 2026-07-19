# SDKs — Python & TypeScript Instrumentation (Phase 3)

> Part of the [Anvil implementation plan](../plan.md). The endpoint these SDKs talk to is specified in [ingestion.md](ingestion.md); the feedback method added later is specified in [evaluation.md](evaluation.md) §7; the eval CLI hosted in the Python SDK is in [evaluation.md](evaluation.md) §5.

**Goal:** A developer instruments their app in <10 lines and traces appear in Anvil. **SDKs must never crash or block the host app** — that is the prime directive; every design choice below serves it.

## 1. Shared design (both languages)

- `init(api_key, base_url, flush_interval=2s, max_queue=1000)`.
- `trace(name, input=…)` context manager / wrapper creating a root trace; `span(name, type, …)` nestable via contextvars (Py) / AsyncLocalStorage (TS). Manual API too: `client.startTrace(...)`, `span.end(output=…)`.
- Decorator/wrapper sugar: `@observe(type="llm")` (Py), `observe(fn, {type})` (TS) — args captured as input, return value as output, exceptions as error status.
- **Background flusher:** bounded in-memory queue → batch POST to `/v1/ingest/traces`; on 429/5xx exponential backoff with jitter; on full queue **drop oldest and count** (never block the host); flush on process exit (atexit / beforeExit).
- **Client-side truncation** mirroring the server policy (100 KB per field) so batches rarely hit the 5 MB body cap; a `redact` init option (`redact: (span) => span` / callable) lets customers scrub PII before anything leaves their process — document it prominently.
- Capture per span type: llm → model, params, prompt template + variables (if given), raw output, provider token usage; retrieval → query, returned chunks (text, source, score); tool → name, args, result.
- Trace IDs are client-generated UUIDs (idempotent ingestion) and exposed as `trace.id` so customers can round-trip them to their frontend (needed for feedback).

## 2. Build steps

1. **Python SDK** (`sdks/python`, package `anvil-sdk`): `httpx` transport, zero heavy deps. Unit tests with respx fake server.
2. **LangChain integration** (Python): `AnvilCallbackHandler(BaseCallbackHandler)` mapping LangChain run trees (llm/chain/retriever/tool starts+ends) to Anvil spans — "instrument a LangChain app in one line", and the demo path.
3. **Demo app** (`examples/demo-rag-app`): tiny FastAPI RAG app (fake retrieval + real-or-mocked LLM via LangChain), instrumented with SDK + callback handler. Doubles as the demo-data generator: `python -m demo seed --n 200` produces varied realistic traces (errors, slow ones, mixed models). Later phases extend it: `--drift` regression mode ([evaluation.md](evaluation.md)), thumbs-down button (feedback).
4. **TypeScript SDK** (`sdks/typescript`, `@anvil/sdk`): mirrors the Python API; fetch-based; Node 18+ (browser out of scope v1). Vitest + msw tests.

## 3. Acceptance

Running the demo app produces a multi-span trace visible via `GET /v1/traces/{id}` with correct parent/child nesting, tokens, and cost. **Killing the Anvil API while the demo app runs does not break the demo app** (verified in a test). Both SDK test suites pass.
