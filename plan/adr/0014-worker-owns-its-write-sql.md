# ADR-0014: The worker persists via explicit SQL, not shared ORM models

Status: Accepted · Date: 2026-07 · Spec: ../ingestion.md §3

## Context
The ingest consumer writes to `traces` and `spans`, whose SQLAlchemy models live in `anvil_api.models`. Sharing them would keep column definitions in one place. But `apps/worker/Dockerfile` builds with `apps/worker/` as its context and cannot `COPY` the API package, so `anvil_worker` importing `anvil_api` would force one of: a repo-root build context for both images, a third shared package, or a published internal dependency — each a real cost paid on every build to avoid retyping a column list.

The write path also can't use the ORM as an ORM. The guarded upsert from ADR-0003 is `INSERT … ON CONFLICT (id) DO UPDATE SET … WHERE traces.project_id = excluded.project_id AND (excluded.ended_at IS NOT NULL OR traces.ended_at IS NULL)`. That SQL is hand-written whichever way the tables are declared.

## Decision
`anvil_worker` owns its write SQL as explicit statements against table/column names, and declares no dependency on `anvil_api`. The API remains the sole owner of the schema (Alembic migrations) and of all read queries.

## Consequences
Images stay independently buildable and the worker's write surface is small, explicit, and readable next to the guards it must enforce. The cost is that a future migration renaming a `traces`/`spans` column must be applied to the worker's SQL by hand — nothing at import time will catch it. That risk is carried by the round-trip test (ingest → persist → read back identical tree), which fails loudly on any column drift. Tests may import both packages; both are `pip install -e` in a dev environment, and that convenience must not leak into runtime code.

## Alternatives considered
- **Worker imports `anvil_api.models`** — requires a repo-root Docker context for the worker, coupling image builds to the API's dependency tree.
- **Extract a third `anvil-shared` models package** — a package, a version, and a release step to hold two table definitions; revisit if a third service needs them.
- **Worker runs its own Alembic** — two migration heads on one database; rejected outright.
