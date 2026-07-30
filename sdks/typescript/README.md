# @anvil/sdk (TypeScript)

Trace instrumentation for LLM apps. Node 18+ (browser is out of scope for v1).
Spec: [`plan/sdk.md`](../../plan/sdk.md).

```bash
npm install @anvil/sdk
```

```ts
import * as anvil from "@anvil/sdk";

anvil.init({ apiKey: "ak_...", baseUrl: "http://localhost:8000" });

await anvil.trace("answer_question", { input: question }, async (t) => {
  const docs = await anvil.span("retrieve", { type: "retrieval" }, () => search(question));
  const answer = await anvil.span("generate", { type: "llm" }, (s) => {
    const out = await llm(docs);
    s.end({ output: out, tokensIn: 120, tokensOut: 30 });
    return out;
  });
  t.output = answer;
  return answer;
});
```

## The guarantee

**This SDK never blocks or crashes your app** ([ADR-0013](../../plan/adr/0013-sdk-never-blocks-host.md)).
Traces go to a bounded in-memory queue shipped by a background timer (`unref`d,
so it never holds the event loop open). If Anvil is slow, down, or
misconfigured: the queue sheds its *oldest* entries, counts them in
`anvil.stats()`, and your request path never notices.

## API

| | |
|---|---|
| `init({ apiKey, baseUrl, flushInterval, maxQueue, redact })` | Configure the global client |
| `trace(name, options?, fn)` | Root of a unit of work; `fn` receives the trace |
| `span(name, options?, fn)` | Nests via `AsyncLocalStorage`, so it survives `await` |
| `observe(fn, { type })` | Wrapper sugar — args in, resolved value out |
| `startTrace(name)` / `startSpan(trace, name)` | Manual API; call `.end({ output })` |
| `feedback({ traceId, rating, comment?, userRef? })` | End-user thumbs up/down (`-1` / `1`) |
| `flush()` / `shutdown()` / `stats()` | Lifecycle and counters |

`trace.id` is a client-generated UUID — hand it to your frontend so feedback can
be attached to that exact trace later.

Span types: `llm`, `retrieval`, `tool`, `embedding`, `chain`, `custom`.

## feedback — closing the loop

Round-trip `trace.id` to your frontend, then send the vote back with it:

```ts
app.post("/ask", async (req, res) => {
  const t = anvil.startTrace("ask");
  const answer = await answerQuestion(req.body.question);
  t.end({ output: answer });
  res.json({ answer, traceId: t.id });
});

app.post("/feedback", (req, res) => {
  anvil.feedback({
    traceId: req.body.traceId,
    rating: -1,
    comment: req.body.comment,
    userRef: req.user.id,
  });
  res.json({ ok: true });
});
```

Two things worth knowing:

- **Call it as soon as the user clicks.** The vote routinely beats its own trace
  to Anvil's database; the server holds it and waits for the trace rather than
  rejecting the race. You never need to await anything first.
- **Pass `userRef` if you have one.** With it, repeat votes upsert — one user
  toggling 👎 → 👍 replaces their row instead of leaving two contradictory ones.
  Without it, every call is an independent anonymous vote.

Fire-and-forget like everything else here: `feedback()` never throws, and its own
counters (`feedbackQueued`/`feedbackSent`/`feedbackDropped`) are separate from the
trace ones so a busy trace queue can't hide a lost vote.

Downvoted traces show up in the dashboard's **failure inbox** — thumbs-down
traces not yet in any dataset — where one click turns a real failure into a test
case.

## redact — scrub PII before it leaves your process

```ts
anvil.init({
  apiKey: "...",
  redact: (obj) => {          // called for every trace *and* every span
    obj.input = mask(obj.input);
    return obj;               // or null to drop the object entirely
  },
});
```

A hook that throws causes the object to be **dropped rather than shipped
un-scrubbed**. Fields over 100 KB are truncated to a preview before sending,
mirroring the server-side policy.

## Tests

```bash
npm install && npm test
```

The transport is a scripted `fetchImpl` injected through `ClientOptions`, so
tests are deterministic without a mock-server layer. Emitted payloads are
validated with Ajv against `packages/shared/trace-schema.json` — the same schema
the ingest endpoint enforces.
