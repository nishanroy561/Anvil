# Demo RAG app

A ~150-line FastAPI question-answering service instrumented with the Anvil
Python SDK. It exists to show two things: what instrumenting a real app looks
like, and what a good trace looks like in the dashboard. It also generates the
demo data every later phase builds against.

The LLM is **mocked by default** — no API key needed, and the whole thing runs
offline. Set `DEMO_LLM=real` (plus `pip install -e .[real-llm]` and an
`ANTHROPIC_API_KEY`) to swap in a live model; nothing else changes.

## Run it

```bash
# 1. Anvil itself, from the repo root
docker compose up -d postgres redis
cd apps/api && python -m alembic upgrade head && python -m anvil_api.seed   # prints an API key
python -m uvicorn anvil_api.main:app                                        # :8000
cd ../worker && python -m anvil_worker.main                                 # the persister

# 2. This app
cd examples/demo-rag-app
pip install -e ../../sdks/python -e .
python -m demo seed --api-key ak_...   --n 200      # generate 200 varied traces
python -m demo serve --api-key ak_...               # or run the API on :8100
```

Then ask it something:

```bash
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"How long do refunds take?","session_id":"sess-1"}' | jq
# => {"answer": "...", "sources": ["kb/refunds", ...], "trace_id": "…"}
```

`trace_id` comes back in the response on purpose: it is a client-generated
UUID, so a frontend can hold onto it and attach a thumbs-down to that exact
trace later (Phase 9).

## The integration, in full

That is the whole thing — one `init()` and one handler:

```python
import anvil_sdk as anvil
from anvil_sdk.langchain import AnvilCallbackHandler

anvil.init(api_key=..., base_url=..., redact=_redact)

handler = AnvilCallbackHandler(trace_name="ask", session_id=session_id)
result = chain.invoke(question, config={"callbacks": [handler]})
```

`_redact` (in `demo/app.py`) is worth reading before you write your own: it
masks emails *recursively*, because span payloads are dicts and lists of
documents, not flat strings. A hook that only checks top-level strings leaks
the data it was added to catch.

## What the seeder produces

`python -m demo seed --n 200` generates traffic that looks like a real app
rather than a benchmark: three models mixed (`claude-sonnet-5`,
`claude-haiku-4-5`, `gpt-4o-mini` — all in the worker's price table, so cost
figures come out non-null), ~8% simulated provider failures, ~12% slow calls
for a latency tail, and twenty sessions across twelve users with `topic` and
`channel` metadata to filter on.

## Killing Anvil

Stop the Anvil API and keep asking questions — the demo app carries on
answering. That is ADR-0013 and it is covered by
`tests/test_demo.py::test_anvil_dying_mid_run_does_not_break_the_app`, which
kills the ingest endpoint mid-run and asserts every subsequent request still
succeeds.

## Tests

```bash
pip install -e .[dev] respx && python -m pytest -q
```

Keyless and offline, per CLAUDE.md.

## Later phases extend this app

- **Phase 8** adds `--drift` to the seeder (regression mode for monitors/alerts).
- **Phase 9** adds a thumbs-down button that posts feedback against `trace_id`.
