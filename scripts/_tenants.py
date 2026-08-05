"""Throwaway-tenant teardown, shared by the acceptance scripts.

Every acceptance script that provisions its own org was leaving it behind. Three
sessions of running them is what surfaced it: the dev database had accumulated
`Loadtest <hex>`, `Kill Test`, `Drift Acceptance`, `OTel Check` and six copies of
`Phase 9 main` — thousands of traces of noise sitting in the operator's own
dashboard, indistinguishable at a glance from real demo data.

`loadtest_reads.py` had always cleaned up; nothing else did. This is that logic,
in one place, so the next script to provision a tenant gets it for free.

**Deletion order follows the foreign keys.** Spans, feedback and monitor scores
go with their traces by `ON DELETE CASCADE`. `dataset_items.source_trace_id` is
`ON DELETE SET NULL`, so a dataset promoted from these traces during a manual
poke survives with its payloads intact — which is the same guarantee retention
makes (ADR-0018), exercised here incidentally.
"""

from __future__ import annotations

import uuid

DEFAULT_DATABASE_URL = "postgresql+asyncpg://anvil:anvil@localhost:5432/anvil"


def dsn(database_url: str) -> str:
    """asyncpg wants a plain `postgresql://` URL, not SQLAlchemy's driver form."""
    return database_url.replace("postgresql+asyncpg", "postgresql")


async def drop_org(database_url: str, org_id: uuid.UUID | str) -> None:
    """Remove one org and everything hanging off it.

    Used by scripts that create their tenant directly in SQL (the drift
    acceptance), where there is no single project to work back from.
    """
    import asyncpg

    conn = await asyncpg.connect(dsn(database_url))
    try:
        await _delete_org(conn, uuid.UUID(str(org_id)))
    finally:
        await conn.close()


async def drop_tenant(database_url: str, project_id: uuid.UUID | str) -> None:
    """Remove the org that owns `project_id`, and everything under it."""
    import asyncpg

    conn = await asyncpg.connect(dsn(database_url))
    try:
        pid = uuid.UUID(str(project_id))
        org_id = await conn.fetchval("SELECT org_id FROM projects WHERE id = $1", pid)
        if org_id is None:
            return  # already gone; a second cleanup pass is not an error
        await _delete_org(conn, org_id)
    finally:
        await conn.close()


def drop_tenant_sync(database_url: str, project_id: uuid.UUID | str) -> None:
    """`drop_tenant` for the synchronous acceptance scripts.

    `compare_acceptance.py` and `ci_gate_acceptance.py` drive the API over plain
    `httpx.Client` and have no event loop of their own, so they get a wrapper
    rather than an async main they do not otherwise need.
    """
    import asyncio

    asyncio.run(drop_tenant(database_url, project_id))


async def _delete_org(conn, org_id: uuid.UUID) -> None:
    """Caller owns the connection. Order matters — see the module docstring.

    Eval results reference runs *and* dataset items, and monitors reference
    traces, so the evaluation tables come down before the traces they point at
    rather than relying on a cascade that these tables deliberately do not have.
    """
    projects = [
        r["id"] for r in await conn.fetch("SELECT id FROM projects WHERE org_id = $1", org_id)
    ]
    if projects:
        await conn.execute(
            "DELETE FROM eval_results WHERE run_id IN "
            "(SELECT id FROM eval_runs WHERE project_id = ANY($1::uuid[]))",
            projects,
        )
        await conn.execute("DELETE FROM eval_runs WHERE project_id = ANY($1::uuid[])", projects)
        await conn.execute(
            "DELETE FROM dataset_items WHERE dataset_id IN "
            "(SELECT id FROM datasets WHERE project_id = ANY($1::uuid[]))",
            projects,
        )
        await conn.execute("DELETE FROM datasets WHERE project_id = ANY($1::uuid[])", projects)
        await conn.execute(
            "DELETE FROM monitor_scores WHERE monitor_id IN "
            "(SELECT id FROM monitor_configs WHERE project_id = ANY($1::uuid[]))",
            projects,
        )
        await conn.execute(
            "DELETE FROM monitor_configs WHERE project_id = ANY($1::uuid[])", projects
        )
        await conn.execute(
            "DELETE FROM alert_events WHERE rule_id IN "
            "(SELECT id FROM alert_rules WHERE project_id = ANY($1::uuid[]))",
            projects,
        )
        await conn.execute("DELETE FROM alert_rules WHERE project_id = ANY($1::uuid[])", projects)
        # Traces last of the project-scoped tables: spans, feedback and any
        # remaining monitor scores cascade from here.
        await conn.execute("DELETE FROM traces WHERE project_id = ANY($1::uuid[])", projects)
        await conn.execute("DELETE FROM api_keys WHERE project_id = ANY($1::uuid[])", projects)
        await conn.execute("DELETE FROM projects WHERE org_id = $1", org_id)

    await conn.execute("DELETE FROM users WHERE org_id = $1", org_id)
    await conn.execute("DELETE FROM org_usage WHERE org_id = $1", org_id)
    await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
