# ADR-0016: The model name picks the LLM provider

Status: Accepted · Date: 2026-07 · Spec: ../evaluation.md §2–3

## Context
[evaluation.md](../evaluation.md) §2 names `claude-sonnet-5` via `ANTHROPIC_API_KEY` as the default judge, and Phase 5 shipped with `ChatAnthropic` constructed inline in two places — the judge and the eval runner's target generation. That is fine as long as Anthropic is the only provider anyone uses, and wrong the moment it isn't.

It stopped being fine immediately. The first machine to run a judged eval had an Anthropic key with no credit balance and a working Google key. A self-hoster's provider is not our choice to make: Anvil is open-core and runs on the operator's own credentials, so "which model judges" is their decision, not ours.

The obvious fix — a `JUDGE_PROVIDER` environment variable — puts provider selection in deployment configuration, where the UI cannot see it and a run's stored config cannot record it. A run that says only "graded by `accuracy`" is not reproducible; six months later nobody can say which model produced the score.

## Decision
**The configured model name resolves the provider.** `claude-*` → Anthropic (`ANTHROPIC_API_KEY`), `gemini-*` → Google (`GOOGLE_API_KEY`). Resolution lives in one module, `anvil_worker/providers.py`, used by both the judge and target generation.

When no model is configured, the first provider with a key present wins, in a fixed order: Anthropic, then Google. Anthropic stays first because evaluation.md names it the default judge; Google is the fallback so a Gemini-only deployment can still run judged evals out of the box.

Keys are read from the process environment, loaded once from the repo-root `.env` by `load_env()`, resolved from the module's own path rather than the working directory.

## Consequences
Provider choice travels *in data*. The grader catalog already exposes a `model` field per judge, so the picker in the dashboard configures the provider with no UI change, and `eval_runs.grader_config` records which model scored a run — Phase 6's comparison can then refuse to compare runs judged by different models, rather than silently averaging across them.

Adding a provider is one entry in `PROVIDERS` plus a branch in `build_chat_model`; the eval runner, the API, and the dashboard are untouched. The cost of that is a naming coupling: a provider whose models don't share a recognisable prefix needs an explicit rule, and two providers serving a same-named model cannot be distinguished. Neither is true of the providers v1 supports.

The fixed fallback order means a deployment holding *both* keys, with a broken Anthropic account, gets Anthropic-by-default failures until it names a Gemini model. That is deliberate: silently routing around a provider outage would hide a billing problem the operator needs to see, and the failure is already legible — a dead key surfaces as a per-item grader error carrying the provider's own message, visible in the run's results table, with the run completing and other graders unaffected.

Reading keys from `os.environ` rather than the worker's pydantic settings is also deliberate. Those settings resolve `.env` against the working directory; the worker launches from `apps/worker` in dev and `/app` in the image, so a settings-sourced key would work from one and not the other. Real environment variables always win over the file, so compose and CI are unaffected.

## Alternatives considered
- **`JUDGE_PROVIDER` env var** — provider becomes invisible to the UI and absent from the run record; every run in a project is stuck on one provider.
- **Explicit `provider` field on each grader config** — redundant with the model name, and lets the two disagree (`provider: anthropic` + `model: gemini-2.5-flash` has no correct reading).
- **Probe each key at startup and prefer whichever answers** — a live API call on every worker boot, coupling startup to provider availability, and a default that changes under you when a provider has a bad minute.
- **A LiteLLM-style universal shim** — a dependency and an abstraction layer to support two providers, when LangChain already normalises `.with_structured_output()` across both.
