# anvil-sdk (Python)

Trace instrumentation for LLM apps. Spec: [`plan/sdk.md`](../../plan/sdk.md).

```bash
pip install anvil-sdk           # + [langchain] for the callback handler
```

```python
import anvil_sdk as anvil

anvil.init(api_key="ak_...", base_url="http://localhost:8000")

with anvil.trace("answer_question", input=question) as t:
    with anvil.span("retrieve", type="retrieval") as s:
        s.output = search(question)
    with anvil.span("generate", type="llm") as s:
        s.end(output=answer, tokens_in=120, tokens_out=30)
    t.output = answer
```

LangChain apps need one line instead:

```python
from anvil_sdk.langchain import AnvilCallbackHandler

chain.invoke(question, config={"callbacks": [AnvilCallbackHandler()]})
```

## The guarantee

**This SDK never blocks or crashes your app** ([ADR-0013](../../plan/adr/0013-sdk-never-blocks-host.md)).
Traces go to a bounded in-memory queue and a background thread ships them. If
Anvil is slow, down, or misconfigured: the queue sheds its *oldest* entries,
counts them in `anvil.stats()`, and your request path never notices. Nothing in
this package raises into your code except exceptions your own functions raised.

## API

| | |
|---|---|
| `init(api_key, base_url, flush_interval=2.0, max_queue=1000, redact=None)` | Configure the global client |
| `trace(name, input=…, metadata=…, session_id=…, user_ref=…)` | Context manager; root of a unit of work |
| `span(name, type=…, input=…)` | Context manager; nests via `contextvars` |
| `@observe(type="llm")` | Decorator sugar — args in, return value out, exceptions marked |
| `start_trace(...)` / `start_span(...)` | Manual API for middleware and callbacks; call `.end(output=…)` |
| `flush()` / `shutdown()` / `stats()` | Lifecycle and counters |

`trace.id` is a client-generated UUID exposed on the object — hand it to your
frontend so a thumbs-down can be attached to that exact trace later.

Span types: `llm`, `retrieval`, `tool`, `embedding`, `chain`, `custom`.

## redact — scrub PII before it leaves your process

```python
anvil.init(api_key=..., redact=scrub)

def scrub(obj):          # called for every trace *and* every span
    obj.input = mask(obj.input)
    return obj           # or None to drop the object entirely
```

Payloads are usually dicts and lists, not flat strings — walk them recursively.
`examples/demo-rag-app/demo/app.py` has a worked example. A hook that raises
causes the object to be **dropped rather than shipped un-scrubbed**.

Fields over 100 KB are truncated to a preview before sending, mirroring the
server-side policy so large payloads degrade instead of 413ing.

## Tests

```bash
pip install -e .[dev] && python -m pytest -q
```

Keyless: LLM calls use LangChain's fake models and the transport is mocked with
respx. Emitted payloads are validated against `packages/shared/trace-schema.json`,
the same schema the ingest endpoint enforces.
