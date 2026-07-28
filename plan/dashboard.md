# Dashboard — Traces & Waterfall UI (Phase 4)

> Part of the [Anvil implementation plan](../plan.md). Read APIs in [ingestion.md](ingestion.md) §5. Eval/dataset UI, monitors UI, and the failure inbox are specified with their subsystems in [evaluation.md](evaluation.md); the Usage panel in [retention.md](retention.md).

**Goal:** The core observability UI — login, project switcher, trace list with filters, and an explorable waterfall trace view.

**UI ground rules (apply to all later UI work too):** load the `frontend-design` skill before building UI so it doesn't look like default Tailwind; invoke the `dataviz` skill before writing any chart code; dark-mode friendly; empty states carry a copy-pasteable SDK setup snippet (with the project's key prefix).

## 1. Web foundation

Typed API client (`src/lib/api.ts`) using the session cookie; auth pages (login/signup); authenticated layout with sidebar (Traces, Datasets, Evals, Monitors, Alerts, Settings) and project switcher; middleware redirects unauthenticated users.

## 2. Traces list (`/p/[projectId]/traces`)

Virtualized table: time, name, status badge, model, duration, tokens, cost, feedback icon. Filter bar: time range, status, name, model, metadata key/value, free-text (server rules in ingestion.md §5). Cursor "load more"; 10 s auto-refresh toggle. Bulk select → "Add to dataset" (wired in evaluation.md).

## 3. Trace detail (`/traces/[traceId]`) — the centerpiece

- Left: indented, collapsible span tree. Main: **waterfall** — horizontal bars positioned/scaled by `started_at`/`ended_at` relative to the trace, colored by span type, duration labels. Pure CSS/flex positioning; no charting lib needed.
- **Resilience (dirty data is normal, not exceptional):** orphan spans (unresolvable parent) render as root-level children; `in_progress`/`abandoned` traces and null-`ended_at` spans render as open-ended bars with a "still running…" / "abandoned" badge; truncated fields show a "content truncated at ingest" notice instead of broken JSON.
- Span click → detail panel: pretty JSON input/output (expand/collapse), prompt template with variables highlighted, retrieved chunks list, tokens/cost/latency, error + stack if failed.
- Trace header: status, totals (latency/tokens/cost), model(s), session/user_ref, attached feedback, **"Add to dataset"** button (stub now; wired in evaluation.md).

## 4. Overview page (`/p/[projectId]`)

Metric cards (traces today, error rate, p95 latency, cost) + small time-series charts from the metrics endpoint (`recharts`; `dataviz` skill first).

## 5. Acceptance

With seeded demo data: login → browse list → filter status=error → open a trace → waterfall renders correct proportions and nesting → span click shows prompt/variables/chunks. A 100-span trace renders without jank.
