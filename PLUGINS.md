# Writing an Anvil plugin

Anvil has three plugin points, all on the same contract:

| What | Entry-point group | Add your own when |
|---|---|---|
| **Grader** | `anvil.graders` | you score outputs by a rule Anvil doesn't have |
| **Alert channel** | `anvil.alert_channels` | you page somewhere other than a generic webhook |
| **Pricing resolver** | `anvil.pricing` | you run a model Anvil has no price for |

A working example of the first two lives in
[`examples/plugin-example/`](examples/plugin-example/) — copy it rather than
starting from this page.

**Built-ins are plugins that ship in-tree.** Every grader Anvil provides registers
through the same mechanism yours will, which is what keeps the contract honest:
there is no privileged path that only internal code can take, so nothing about it
can quietly rot.

---

## 1. The contracts

Nothing you write imports from `anvil_worker`. The registries are duck-typed:
Anvil reads attributes and calls a coroutine. That means your plugin does not
depend on Anvil's internals and cannot be broken by them being refactored.

### Grader

```python
class MyGrader:
    name = "my_grader"                 # what operators pick in the UI
    kind = "deterministic"             # or "judge"
    compares_expected = False

    async def grade(self, output, context, config):
        return Result(score=1.0, detail={"why": "..."})
```

| Attribute | Meaning |
|---|---|
| `name` | The string operators select and write in configs. Must be unique. |
| `kind` | `"deterministic"` (no model, tight timeout) or `"judge"` (gets a model, longer timeout). Omitted ⇒ deterministic. |
| `compares_expected` | `True` only if you score the output *against* `context.expected_output`. Omitted ⇒ `False`. |

`compares_expected` is worth understanding rather than copying. Anvil uses it to
mark a score **tautological** when both sides are the same value — which is
exactly what a rescore run over unedited promoted items produces, since promotion
copies the recorded output into `expected_output`
([ADR-0018](plan/adr/0018-recorded-output-is-kept-apart-from-expected-output.md)).
Set it correctly and Anvil will tell operators when your grader is measuring
nothing. Leave it off and an unaware plugin is simply never flagged, which is the
safe default but also a missed warning.

**`context`** carries `input`, `expected_output`, `chunks`, `metadata`, and
`model_factory`. Use `model_factory` for anything LLM-backed — it hands you a
configured LangChain model, so a plugin never manages credentials or has to know
which provider is in play.

**Return value** is read by attribute: `.score` (always 0..1), `.detail`,
`.reasoning`, `.tokens_in`, `.tokens_out`, `.model`. Populate the token fields on
judge graders so the run's cost reflects what judging actually spent. Define your
own small dataclass — see the example — rather than importing Anvil's.

### Alert channel

```python
class MyChannel:
    name = "my_channel"

    async def send(self, event, rule, config) -> None:
        ...  # raise on failure
```

`event` carries `value`, `fired_at`, `resolved_at`; `rule` carries `name`,
`metric`, `comparison`, `threshold`, `window_minutes`, `project_id`. A
`resolved_at` of `None` means the alert is firing; set means it recovered.

### Pricing resolver

```python
def resolve(model: str) -> tuple[float, float] | None:
    """USD per 1M input tokens, per 1M output tokens. None if you don't know."""
```

Return `None` for models you don't recognise so the next resolver gets a turn.
**Never guess a price** — Anvil reports `null` cost for an unknown model, and a
null is honest where a wrong number is worse than no number.

---

## 2. Packaging

```toml
[project]
name = "anvil-plugin-mine"
dependencies = []          # see below

[project.entry-points."anvil.graders"]
my_grader = "anvil_plugin_mine.grader:MyGrader"

[project.entry-points."anvil.alert_channels"]
my_channel = "anvil_plugin_mine.channel:MyChannel"
```

The value points at a **zero-argument callable returning the instance** — Anvil
calls `entry_point.load()()`. A class works because calling it constructs one.

**Keep `dependencies` empty if you can.** `httpx` is already in the worker image
and judges get their model from `model_factory`, so the common needs are already
met. Every pin you add is a version you might fight the host over.

### Installing into the compose images

The worker image installs from `apps/worker`, so a plugin needs to be present at
build time. Add it to your own image layered on top:

```dockerfile
FROM anvil-worker:latest
COPY my-plugin /opt/my-plugin
RUN pip install --no-cache-dir /opt/my-plugin
```

For local development, `pip install -e examples/plugin-example` into the same
environment you run `python -m anvil_worker.main` from.

---

## 3. What Anvil guarantees, and what it expects

### It will not let you take a run down

Every grader call is wrapped in a timeout and a `try/except`. A grader that
raises, or one that hangs, produces an **item-level grader error**: excluded from
that grader's mean rather than averaged in as a zero, and counted in
`errored_items`. The run finishes. Same for channels — one that fails or hangs
never blocks another channel, or the next alert.

So **do not be defensive on Anvil's behalf.** Raise when something is wrong. The
one thing a plugin must never do is swallow an error to look successful: that
turns a dead webhook into an alert nobody knows was never delivered, and a broken
grader into a confident `1.0`.

### Timeouts are not advisory

Deterministic graders get `GRADER_TIMEOUT_S` (30s default); judges get
`JUDGE_TIMEOUT_S` (120s). Channels get 10s, because they talk to the network and a
hanging webhook must not hold the alerter's cycle open. Exceed it and your call is
cancelled and recorded as an error.

### A name collision is rejected, loudly

First registration wins, and the duplicate is logged at ERROR and dropped. Nothing
silently shadows anything. Built-ins register first, so you cannot override
`exact_match` by claiming its name — pick another.

### Config is yours to validate

Anvil validates **built-in** grader configs against a JSON Schema before a run
starts, and 400s a bad one. It cannot do that for your plugin, because it has no
schema for it. So check your own inputs, and prefer returning a scored result with
an explanatory `detail` over raising — the operator is already looking at the
results table. Defaults that pass everything are better than defaults that fail
everything: a misconfigured grader should look visibly useless, not confidently
strict.

### Shared deployments load built-ins only

With `DEPLOYMENT_MODE=cloud` the registries skip external entry points entirely and
log that they did
([ADR-0012](plan/adr/0012-plugin-system.md)). Arbitrary third-party Python must
not execute next to other tenants' data. Nothing about your plugin changes — the
host declines to load it. On `self_hosted` (the default, and the only mode most
installs use) everything loads.

### Discovery failure is never fatal

A plugin whose import raises is logged and skipped; the others still load. If
entry-point discovery itself fails, Anvil runs with built-ins only. You cannot
prevent Anvil from starting.

---

## 4. Checking it works

```bash
pip install -e path/to/my-plugin
cd apps/worker && python -m anvil_worker.main
```

Look for `loaded grader plugin 'my_grader'`. Then confirm the operator-facing half:
your grader should appear in the eval-run picker and the monitor form with no
frontend change, because both read `GET /v1/graders` off the same registry.

`apps/worker/tests/test_plugins.py` is the suite Anvil's own example is held to —
discovery, a raising plugin, a hanging plugin, a name collision, and cloud mode.
Worth reading before you trust your own.
