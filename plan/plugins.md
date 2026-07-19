# Plugin System — Extensibility Without Forking (cross-cutting)

> Part of the [Anvil implementation plan](../plan.md). Decision record: [ADR-0012](adr/0012-plugin-system.md). Registries are built with their subsystems (phases noted below); discovery + example plugin + author docs land in Phase 11.

**Why:** Anvil is open-core ([pricing.md](pricing.md) §0). Self-hosters who can't extend the platform fork it, and a fork is the failure mode of the whole model. The highest-leverage extension points for an LLM observability platform are graders, alert channels, and model pricing — so those are the v1 plugin surface. Deliberately nothing else (no UI plugins, no ingestion transforms) — small surface, honored contract.

## 1. Extension points

| Surface | Entry-point group | Contract (Python `Protocol`) | Built with |
|---|---|---|---|
| **Graders** | `anvil.graders` | `name: str`, `config_schema: type[BaseModel]`, `async grade(input, output, context) -> GradeResult(score: 0..1, detail: dict)` | Phase 5 ([evaluation.md](evaluation.md) §2) |
| **Alert channels** | `anvil.alert_channels` | `name: str`, `config_schema: type[BaseModel]`, `async send(event, rule, config) -> None` | Phase 8 ([evaluation.md](evaluation.md) §6) |
| **Model pricing** | `anvil.pricing` | `resolve(model: str) -> Pricing(in_per_mtok, out_per_mtok) \| None` | Phase 2 ([ingestion.md](ingestion.md) §4) |

`context` for graders carries what judges need: expected_output, retrieved chunks, dataset-item metadata, and (for LLM-backed plugins) a configured LangChain model handle so plugins don't manage their own keys.

## 2. Registry & discovery

- One registry per surface (`anvil_worker/registry.py`), populated at api/worker startup: **built-ins register through the same contract** — `valid_json`, the LLM judges, the webhook channel, the static pricing table are all just plugins that ship in-tree. Dogfooding the contract is what keeps it honest.
- External plugins: standard Python packages exposing entry points; discovered via `importlib.metadata.entry_points(group="anvil.graders")` etc. A self-hoster installs one by adding `pip install anvil-plugin-x` to their worker/api image (document the Dockerfile override).
- Name collisions: first registration wins (built-ins load first); the loser is rejected with a loud log line, never silently shadowed.
- **The UI is registry-driven:** grader and alert-channel pickers render whatever the registry exposes (name + `config_schema` drives the form via JSON Schema). Installed plugins appear automatically — no UI changes needed to adopt one.

## 3. Isolation (a broken plugin must never take Anvil down)

- Every plugin call is wrapped: per-call try/except + timeout (graders 30 s, channels 10 s, pricing 1 s — env-tunable).
- Failure routing follows each subsystem's existing error path: grader failure → item-level grader error (excluded from aggregates per [evaluation.md](evaluation.md) §3's aggregation rule); channel failure → logged, `alert_events.notified = false`; pricing failure → cost null. A hung or crashing plugin never kills a worker, fails a batch, or blocks an alert from reaching other channels.
- Plugin load failure at startup: log + skip, never refuse to boot.

## 4. Cloud policy

**Cloud mode loads built-ins only** (checked via `DEPLOYMENT_MODE`, same flag as [pricing.md](pricing.md) §0). Running arbitrary tenant Python server-side is a security boundary we do not cross in v1 — no sandbox, no marketplace. The plugin system serves self-hosters and our own modules; a curated/marketplace or WASM-sandboxed story is explicitly future work ([ADR-0012](adr/0012-plugin-system.md)).

## 5. Phase 11 deliverables

- Entry-point discovery wired and documented (`PLUGINS.md` author guide: contract, packaging, install into the compose images).
- `examples/plugin-example/`: a real installable package with one grader (`word_count_bounds`) and one alert channel (Slack-formatted webhook) — used by the tests below and as the author template.
- Tests: example plugin discovered and usable end-to-end (appears in the UI picker, grades a run, delivers an alert); a plugin that raises and one that hangs — run completes, worker survives, timeout honored; name collision rejected loudly; cloud mode ignores external plugins.
