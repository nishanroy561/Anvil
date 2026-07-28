# ADR-0020 — Inference hosts are addressed with a routing prefix

Status: **Accepted** — 2026-07-28 · Spec: [../evaluation.md](../evaluation.md)
Extends: [ADR-0016](0016-model-name-picks-the-provider.md)

## Context

ADR-0016 settled that the configured model name resolves the provider:
`claude-*` → Anthropic, `gemini-*` → Google. That works because those names are
*model families owned by the provider serving them*. The name carries the
routing information for free.

Groq breaks the assumption. Groq is an inference host, not a model maker, and
the models on this key are:

```
llama-3.3-70b-versatile     openai/gpt-oss-120b      qwen/qwen3.6-27b
llama-3.1-8b-instant        openai/gpt-oss-20b       groq/compound
```

Two distinct problems:

1. **`llama-3.3-70b-versatile` says nothing about who serves it.** Llama is
   served by Groq, Together, Fireworks, Bedrock, Ollama and others, at different
   prices and rate limits. A bare Llama name cannot resolve a provider even in
   principle.
2. **`openai/gpt-oss-120b` actively misleads.** Prefix-matching that name to
   OpenAI would be wrong: it is an open-weights model hosted by Groq, priced at
   $0.15/$0.60 per Mtok, reached with a Groq key at a Groq endpoint. A rule that
   sends it to `api.openai.com` fails in the most confusing way available.

This is not a Groq quirk. Every inference host — Together, Fireworks, OpenRouter,
a local vLLM — has the same shape, so whatever is decided here is the pattern
for all of them.

## Decision

**Models on an inference host are addressed with an explicit routing prefix
naming the host**, and only the first path segment is the prefix:

| Configured model | Provider | Model id sent |
|---|---|---|
| `groq/llama-3.3-70b-versatile` | Groq | `llama-3.3-70b-versatile` |
| `groq/openai/gpt-oss-120b` | Groq | `openai/gpt-oss-120b` |
| `groq/groq/compound` | Groq | `groq/compound` |
| `claude-sonnet-5` | Anthropic | `claude-sonnet-5` |

ADR-0016's mechanism is unchanged — the name still resolves the provider,
`PROVIDERS` still holds `(env var, prefixes, default model)`, and nothing in
the eval runner, the API or the dashboard knows a new provider was added. What
changes is that the prefix is *explicit* rather than inferred from a family
name.

The third row is admittedly awkward, and it is the correct behaviour: `groq/` is
the route, `groq/compound` is what Groq calls the model. Stripping every leading
`groq/` would make that model unreachable.

**Prices are keyed on the full prefixed name** (`groq/openai/gpt-oss-120b`), not
the bare id. The host sets the price, not the model family, and one deployment
could plausibly use both `gpt-oss` on Groq and a direct OpenAI model.
`groq/compound` is deliberately absent from the table — Groq passes its cost
through to whichever models the compound system selects, so there is no
per-token price to state, and an unknown model yields a null cost rather than
an invented one.

## Consequences

- Adding Together, Fireworks or a local vLLM is one `PROVIDERS` row and one
  branch in `build_chat_model`, with no ambiguity about what `llama-3.3-70b`
  means.
- **Users must type the prefix**, and forgetting it produces "no judge key
  configured" rather than a wrong-provider call. That is the better failure: it
  names the problem instead of billing the wrong account.
- The fixed-order fallback for an unnamed model now has three providers rather
  than two, which makes the existing trap worse: a stale `ANTHROPIC_API_KEY`
  captures every judge call on a box that also has Google and Groq keys. The
  order is unchanged (ADR-0016 settled it), and `.env.example` now says plainly
  to name the model.
- Groq's own `groq/compound` reads as `groq/groq/compound` in config. Ugly,
  unambiguous, and rare.

## Alternatives rejected

**Enumerate Groq's model ids as prefixes.** Breaks every time Groq adds a
model, and cannot resolve `llama-*` if a second Llama host is ever added.

**A separate `provider` config field alongside `model`.** Cleaner in isolation,
but it is exactly what ADR-0016 rejected: it moves provider choice out of the
single string that already round-trips through the API, the UI, the eval config
and the CI YAML, and every one of those surfaces would need a new field.

**Route on the key that happens to be set.** Silent and unpredictable — the
same config would call different providers on different machines.
