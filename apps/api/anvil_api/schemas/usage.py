"""Usage rollup wire shapes (plan/retention.md §2).

No quota field, no percentage-of-limit, no `plan` — Anvil has no tiers
(ADR-0019). These numbers exist so an operator can see how much their app is
producing and how much judging is costing them, and for nothing else.
"""

from datetime import date, datetime

from pydantic import BaseModel


class UsageMonth(BaseModel):
    """One org-month. Counters only ever move up (ADR-0022): `traces_ingested`
    means "ingested during this month", so a retention sweep that removes the
    traces does not reduce it."""

    month: date
    traces_ingested: int
    eval_items: int
    # Always null. Per-org bytes are not honestly attributable (ADR-0022); the
    # field stays on the wire because the column does.
    storage_bytes: int | None = None
    updated_at: datetime | None = None


class UsageSummary(BaseModel):
    months: list[UsageMonth]
    # Whether a retention window is configured, and how wide. `null` means traces
    # are kept forever — the panel has to say which, or "12,000 traces" reads as a
    # standing total when it may be a 30-day one.
    retention_days: int | None = None
    # Pulled out for the panel's headline so it doesn't have to know how months
    # are keyed. Absent until the worker's first daily pass.
    current_month: UsageMonth | None = None
