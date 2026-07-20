"""Read APIs for the dashboard (plan/ingestion.md §5). Session auth.

Two performance rules are load-bearing here and are the reason queries are
hand-built rather than composed from generic filters:

- Free text runs against `search_text` (trigram GIN, ADR-0005) and *never*
  ILIKE over JSONB — that seq-scans and dies in the hundreds of thousands of
  rows (checklist row 26).
- Pagination is keyset on `(started_at, id)`, not OFFSET, so page 500 costs
  what page 1 costs.
"""

import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..config import get_settings
from ..db import get_session
from ..deps import NOT_FOUND, owned_project
from ..models import Span, Trace, User
from ..schemas.traces import (
    MetricsOverview,
    TimeBucket,
    TraceDetail,
    TraceListPage,
    TraceSummary,
)

router = APIRouter(prefix="/v1", tags=["traces"])

METADATA_PREFIX = "metadata."


def _encode_cursor(started_at: datetime, trace_id: uuid.UUID) -> str:
    raw = f"{started_at.isoformat()}|{trace_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, trace_id = raw.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(trace_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(
            400, detail={"code": "invalid_cursor", "message": "Malformed pagination cursor"}
        )


def _metadata_filters(request: Request) -> list:
    """`?metadata.user_tier=pro` → a JSONB containment match.

    Containment (`@>`) hits `ix_traces_metadata_gin`; comparing extracted text
    would not.
    """
    clauses = []
    for param, value in request.query_params.multi_items():
        if param.startswith(METADATA_PREFIX) and len(param) > len(METADATA_PREFIX):
            clauses.append(Trace.meta.contains({param[len(METADATA_PREFIX):]: value}))
    return clauses


@router.get("/projects/{project_id}/traces", response_model=TraceListPage)
async def list_traces(
    project_id: uuid.UUID,
    request: Request,
    q: str | None = Query(None, description="Free text over trace name + root input/output"),
    status: str | None = None,
    name: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1),
    cursor: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    project = await owned_project(project_id, user, session)
    limit = min(limit, settings.traces_page_size_max)

    filters = [Trace.project_id == project.id]

    # Free text is the expensive path, so it is fenced: a trigram floor plus a
    # server-enforced time bound keep cost capped when selectivity is poor.
    if q is not None:
        if len(q.strip()) < settings.search_min_chars:
            raise HTTPException(
                400,
                detail={
                    "code": "query_too_short",
                    "message": f"Search needs at least {settings.search_min_chars} characters",
                },
            )
        window_start = since or datetime.now(timezone.utc) - timedelta(
            days=settings.search_default_days
        )
        oldest_allowed = datetime.now(timezone.utc) - timedelta(days=settings.search_max_days)
        if window_start < oldest_allowed:
            window_start = oldest_allowed
        filters.append(Trace.started_at >= window_start)
        filters.append(Trace.search_text.ilike(f"%{q.strip().lower()}%"))
    elif since is not None:
        filters.append(Trace.started_at >= since)

    if until is not None:
        filters.append(Trace.started_at <= until)
    if status is not None:
        filters.append(Trace.status == status)
    if name is not None:
        filters.append(Trace.name == name)
    if model is not None:
        filters.append(Trace.model == model)
    filters.extend(_metadata_filters(request))

    if cursor is not None:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        # Keyset: strictly "older than" the last row of the previous page.
        filters.append(
            tuple_(Trace.started_at, Trace.id) < tuple_(cursor_ts, cursor_id)
        )

    rows = await session.execute(
        select(Trace)
        .where(and_(*filters))
        .order_by(Trace.started_at.desc(), Trace.id.desc())
        .limit(limit + 1)  # one extra row tells us whether another page exists
    )
    traces = list(rows.scalars())

    next_cursor = None
    if len(traces) > limit:
        traces = traces[:limit]
        next_cursor = _encode_cursor(traces[-1].started_at, traces[-1].id)

    return TraceListPage(
        items=[_summary(t) for t in traces],
        next_cursor=next_cursor,
    )


def _summary(trace: Trace) -> TraceSummary:
    return TraceSummary(
        id=trace.id,
        name=trace.name,
        status=trace.status,
        model=trace.model,
        latency_ms=trace.latency_ms,
        total_tokens_in=trace.total_tokens_in,
        total_tokens_out=trace.total_tokens_out,
        total_cost_usd=float(trace.total_cost_usd) if trace.total_cost_usd is not None else None,
        session_id=trace.session_id,
        user_ref=trace.user_ref,
        metadata=trace.meta,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
    )


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(
    trace_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    trace = await session.get(Trace, trace_id)
    if trace is None:
        raise NOT_FOUND
    # Confirms the trace's project is in the caller's org — 404s otherwise.
    await owned_project(trace.project_id, user, session)

    rows = await session.execute(
        select(Span).where(Span.trace_id == trace.id).order_by(Span.started_at, Span.id)
    )
    detail = _summary(trace).model_dump()
    detail["input"] = trace.input
    detail["output"] = trace.output
    detail["spans"] = list(rows.scalars())
    return TraceDetail.model_validate(detail)


@router.get("/projects/{project_id}/metrics/overview", response_model=MetricsOverview)
async def metrics_overview(
    project_id: uuid.UUID,
    since: datetime | None = None,
    until: datetime | None = None,
    interval: str = Query("hour", pattern="^(hour|day)$"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Aggregates over a time window, bucketed in UTC (`date_trunc`).

    Bucketing is UTC-only by convention — timezone rendering is the frontend's
    job (checklist row 14).
    """
    project = await owned_project(project_id, user, session)
    window_start = since or datetime.now(timezone.utc) - timedelta(days=7)
    filters = [Trace.project_id == project.id, Trace.started_at >= window_start]
    if until is not None:
        filters.append(Trace.started_at <= until)

    is_error = func.count().filter(Trace.status == "error")
    totals = (
        await session.execute(
            select(
                func.count().label("total"),
                is_error.label("errors"),
                func.percentile_cont(0.5)
                .within_group(Trace.latency_ms.asc())
                .label("p50"),
                func.percentile_cont(0.95)
                .within_group(Trace.latency_ms.asc())
                .label("p95"),
                func.coalesce(func.sum(Trace.total_cost_usd), 0).label("cost"),
                func.coalesce(func.sum(Trace.total_tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(Trace.total_tokens_out), 0).label("tokens_out"),
            ).where(and_(*filters))
        )
    ).one()

    bucket = func.date_trunc(interval, Trace.started_at).label("bucket")
    bucket_rows = (
        await session.execute(
            select(
                bucket,
                func.count().label("traces"),
                is_error.label("errors"),
                func.coalesce(func.sum(Trace.total_cost_usd), 0).label("cost"),
                func.coalesce(func.sum(Trace.total_tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(Trace.total_tokens_out), 0).label("tokens_out"),
            )
            .where(and_(*filters))
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()

    return MetricsOverview(
        total_traces=totals.total,
        error_rate=(totals.errors / totals.total) if totals.total else 0.0,
        p50_latency_ms=float(totals.p50) if totals.p50 is not None else None,
        p95_latency_ms=float(totals.p95) if totals.p95 is not None else None,
        total_cost_usd=float(totals.cost),
        total_tokens_in=int(totals.tokens_in),
        total_tokens_out=int(totals.tokens_out),
        interval=interval,
        buckets=[
            TimeBucket(
                bucket=r.bucket,
                traces=r.traces,
                errors=r.errors,
                cost_usd=float(r.cost),
                tokens_in=int(r.tokens_in),
                tokens_out=int(r.tokens_out),
            )
            for r in bucket_rows
        ],
    )
