"""Usage rollups for Settings → Usage (plan/retention.md §2). Session auth.

Read-only, and org-scoped from the session rather than from a path parameter:
usage is a property of the organization, and there is no route by which one org
could ask for another's.

The API only *reads* `org_usage` — the worker's daily pass owns writing it
(ADR-0022). Recomputing here would put a full table scan on a dashboard request.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..config import get_settings
from ..db import get_session
from ..models import OrgUsage, User
from ..schemas.usage import UsageMonth, UsageSummary

router = APIRouter(prefix="/v1", tags=["usage"])


def _retention_days() -> int | None:
    """The window both services read from the same environment. The API never
    acts on it — it does not import from `apps/worker` (ADR-0014) and would not
    delete anything if it could; it reports it so the panel can say whether a
    count is a standing total or a rolling one.

    A window below 1 is reported as unset, matching the worker's refusal to
    interpret it (a 0-day window would delete traffic still arriving)."""
    days = get_settings().retention_days
    return days if days is not None and days >= 1 else None


def _month_start(when: datetime) -> date:
    return when.date().replace(day=1)


@router.get("/usage", response_model=UsageSummary)
async def get_usage(
    months: int = Query(12, ge=1, le=60, description="How many recent months to return"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(OrgUsage)
            .where(OrgUsage.org_id == user.org_id)
            .order_by(OrgUsage.month.desc())
            .limit(months)
        )
    ).scalars()

    entries = [
        UsageMonth(
            month=r.month,
            traces_ingested=r.traces_ingested,
            eval_items=r.eval_items,
            storage_bytes=r.storage_bytes,
            updated_at=r.updated_at,
        )
        for r in rows
    ]

    this_month = _month_start(datetime.now(timezone.utc))
    return UsageSummary(
        # Oldest first: the panel reads as a timeline, and a chart would otherwise
        # have to reverse it.
        months=list(reversed(entries)),
        retention_days=_retention_days(),
        current_month=next((e for e in entries if e.month == this_month), None),
    )
