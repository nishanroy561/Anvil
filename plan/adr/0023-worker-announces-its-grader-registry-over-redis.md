# ADR-0023 — The worker announces its grader registry over Redis

Status: **Accepted** — 2026-07-30 · Spec: [../plugins.md](../plugins.md) §2/§5
Builds on: [ADR-0012](0012-plugin-system.md), [ADR-0014](0014-worker-declares-its-own-tables.md)

## Context

Graders are discovered by entry point **in the worker**, because that is the
process that runs them. `GET /v1/graders` drives the eval-run picker and the
monitor form, and it is served **from the API**, from the vendored copy of
`packages/shared/graders.json`.

So the API can only offer built-ins. Installing a plugin gave an operator a grader
the worker can run and the UI will not show. The name is still accepted on
`POST /v1/eval-runs` — unknown graders pass validation deliberately, since the API
cannot know whether the worker has one — so the feature *worked*, provided you
knew the name existed and typed it correctly into a form that offered you a
different list.

[plugins.md](../plugins.md) §5 asks for the opposite in as many words: the example
plugin must be *"discovered and usable end-to-end (appears in the UI picker,
grades a run, delivers an alert)"*. Phase 11 is where that was noticed — while
writing `PLUGINS.md`, which had confidently claimed the picker showed plugins.

The obvious fix is not available. [ADR-0014](0014-worker-declares-its-own-tables.md)
keeps the API from importing `apps/worker` at all, and it is right to: they are
separately deployable services and the worker image is where third-party Python
lands. The API cannot enumerate what it cannot import.

## Decision

**The worker publishes its live grader registry to Redis, and the API merges it
into `GET /v1/graders`.**

- Key `anvil:registry:graders`, a JSON snapshot written by the worker at startup
  and refreshed periodically, with a TTL a few times the refresh interval.
- The API reads it, drops any entry whose name collides with a built-in, and
  returns built-ins first followed by plugin entries marked `source: "plugin"`.
- If the key is missing, empty, unparseable, or Redis is down, the API serves
  built-ins alone. Redis is already a hard dependency of ingest, but the *picker*
  degrading to built-ins is strictly better than an error page.

Redis is the right channel because it is already the integration bus between
these two services — traces, eval jobs and feedback all cross it. This adds a
message, not a dependency. No code is imported in either direction, so ADR-0014
holds exactly as before.

The **TTL is what makes this safe to be wrong about.** Uninstall a plugin and
restart the worker, and the stale entry disappears on its own; stop the worker
entirely and the picker falls back to built-ins within the window rather than
advertising graders nothing can run.

Plugins may declare `description` and `config_schema` (a JSON Schema dict, or a
pydantic model). Both are optional: a plugin with no schema is offered with a
free-form config editor rather than being hidden.

## Consequences

- Installing a plugin and restarting the worker is now enough to use it from the
  UI. That was the point.
- **The picker can briefly disagree with reality** — for up to the TTL after a
  plugin is removed, or for the seconds before a fresh worker publishes. A grader
  the worker cannot resolve already fails at the item level with a recorded error
  ([evaluation.md](../evaluation.md) §3), so the worst case is a run whose items
  carry `unknown grader` — visible, bounded, and already a supported outcome.
- **Config validation stays built-ins-only.** The API validates a built-in's
  config against the catalog's schema and 400s a bad one; for plugins it now has
  a schema it *could* validate against, but that schema arrived over Redis from
  the very process being configured. Trusting it to reject an operator's input
  would let a buggy plugin lock its own users out of a form. Announced schemas
  drive the UI; they do not gate requests.
- One more piece of runtime state to reason about. Mitigated by it being
  advisory, TTL'd, and fail-soft in every direction.

## Alternatives rejected

- **Leave it, and document that plugins aren't in the picker.** Honest, and the
  cheapest. Rejected because it makes the plugin system something you can only
  use if you already know what you installed — and §5 asks for the opposite.
- **API imports the worker's registry.** Deletes ADR-0014 to save a Redis key,
  and cannot work at all once the worker image carries plugins the API image does
  not.
- **Plugins register themselves in Postgres.** Durable, which is the wrong
  property: a stale row outlives the process that could honour it, and now needs
  a migration and a cleanup path. The TTL is doing real work here.
- **Have the API shell out to the worker, or add a worker HTTP endpoint.** A new
  network surface and a new failure mode, to move data that already has a bus.
