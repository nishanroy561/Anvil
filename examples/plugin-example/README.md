# Anvil plugin example

A real, installable plugin with one grader and one alert channel. It is both the
author template and the fixture the isolation tests run against.

Full author guide: [`PLUGINS.md`](../../PLUGINS.md) at the repo root.

## What's in it

| | Entry-point group | Name |
|---|---|---|
| [`grader.py`](anvil_plugin_example/grader.py) | `anvil.graders` | `word_count_bounds` |
| [`channel.py`](anvil_plugin_example/channel.py) | `anvil.alert_channels` | `slack` |

`word_count_bounds` scores 1.0 when the output's word count falls inside
`min_words`/`max_words`. `slack` sends the same alert the built-in webhook does,
reshaped into Slack's Block Kit — which is the case the channel registry exists
for, since Anvil's own webhook is deliberately vendor-neutral.

## Try it

```bash
pip install -e examples/plugin-example        # into the same env as the worker
cd apps/worker && python -m anvil_worker.main
```

The log lines to look for:

```
loaded grader plugin 'word_count_bounds'
loaded alert channel plugin 'slack'
```

`word_count_bounds` then appears in the eval-run grader picker and the monitor
form with no frontend change — both are driven by `GET /v1/graders`, which reads
the same registry.

## Notice what isn't here

- **No `anvil-worker` dependency.** A plugin implements a duck-typed protocol — a
  `name` and a coroutine — so it neither imports Anvil's internals nor breaks when
  they are refactored. `grader.py` returns its own `Result` dataclass for exactly
  this reason.
- **No dependencies at all.** `httpx` is already in the worker image, and judges
  get a configured model through `context.model_factory` rather than managing
  credentials. Pinning either is how a plugin ends up fighting the host.
- **No error handling on Anvil's behalf.** The worker wraps every call in its own
  timeout and try/except: a grader that raises costs one score, a channel that
  hangs costs one delivery. Swallowing errors to look successful is the one thing
  a plugin must not do — it turns a dead webhook into an alert nobody knows was
  never delivered.

## It won't load on a shared deployment

With `DEPLOYMENT_MODE=cloud` the worker registers built-ins only and logs that it
skipped external plugins ([ADR-0012](../../plan/adr/0012-plugin-system.md)):
arbitrary third-party Python must not execute next to other tenants' data. Nothing
about the plugin changes — the host refuses to load it.
