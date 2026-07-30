"""Feedback submission and the failure inbox (plan/evaluation.md §7).

Two audiences, two credentials, and the split is deliberate:

- `POST /v1/feedback` is **API-key** authed. It is called by a customer's own
  backend on behalf of their end users, which makes it the most abuse-exposed
  surface after ingest — hence a per-key limit (checklist row 19).
- The inbox is **session** authed. It is a dashboard read, and nothing in CI or
  an SDK has any use for it.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ApiKeyContext, current_api_key_context, current_user
from ..config import get_settings
from ..db import get_session
from ..deps import owned_project
from ..models import DatasetItem, Feedback, Trace, User
from ..redis_client import get_redis
from ..schemas.feedback import (
    FailureInboxItem,
    FailureInboxPage,
    FeedbackAccepted,
    FeedbackCreate,
    FeedbackOut,
)
from ..services.feedback import count_rejection, publish_feedback
from ..services.rate_limit import enforce_rate_limit
from .traces import feedback_counts, summary_with_feedback

router = APIRouter(prefix="/v1", tags=["feedback"])
log = logging.getLogger(__name__)


@router.post("/feedback", status_code=202, response_model=FeedbackAccepted)
async def submit_feedback(
    body: FeedbackCreate,
    context: ApiKeyContext = Depends(current_api_key_context),
):
    """Queue one thumbs up/down. 202, never 201 — the row is not written yet.

    Note what is *not* checked here: whether the trace exists. That lookup is
    the worker's job precisely because the answer may still be "not yet"
    (checklist row 12), and answering 404 on a race would teach customers to
    add a sleep before every thumbs-down.
    """
    settings = get_settings()
    redis = get_redis()
    key_id = str(context.api_key.id)

    try:
        await enforce_rate_limit(
            redis, "feedback", key_id, settings.feedback_rate_limit_pm
        )
    except HTTPException:
        count_rejection("rate_limited")
        raise

    comment = body.comment
    if comment is not None and len(comment) > settings.feedback_max_comment_chars:
        # Truncated rather than refused: the rating is the signal worth keeping,
        # and a 422 would throw away a legitimate thumbs-down over a long essay.
        comment = comment[: settings.feedback_max_comment_chars] + "…[truncated]"

    feedback_id = uuid.uuid4()
    try:
        await publish_feedback(
            redis,
            feedback_id=feedback_id,
            trace_id=body.trace_id,
            project_id=context.project.id,
            rating=body.rating,
            comment=comment,
            user_ref=body.user_ref,
            maxlen=settings.feedback_stream_maxlen,
        )
    except RedisError:
        count_rejection("buffer_unavailable")
        log.error("redis XADD failed at feedback; returning 503 so clients retry")
        raise HTTPException(
            503,
            detail={
                "code": "buffer_unavailable",
                "message": "Feedback buffer unavailable; retry shortly",
            },
        )

    return FeedbackAccepted(id=feedback_id)


@router.get("/projects/{project_id}/failure-inbox", response_model=FailureInboxPage)
async def failure_inbox(
    project_id: uuid.UUID,
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Thumbs-down traces that are not yet in any dataset.

    The `NOT EXISTS` over `dataset_items.source_trace_id` is what makes this an
    *inbox* rather than a list: promoting a trace is what clears it, so the queue
    drains as the failures become test cases. Scoped to negative feedback only —
    a thumbs-up needs no triage.

    Grouped by trace, because one trace can collect several downvotes and a
    triage queue that shows the same failure four times is a worse queue.
    """
    settings = get_settings()
    project = await owned_project(project_id, user, session)
    limit = min(limit, settings.failure_inbox_page_size_max)

    promoted = (
        select(DatasetItem.id)
        .where(DatasetItem.source_trace_id == Feedback.trace_id)
        .exists()
    )
    base = (
        select(Feedback.trace_id, func.max(Feedback.created_at).label("latest"))
        .where(
            Feedback.project_id == project.id,
            Feedback.rating < 0,
            ~promoted,
        )
        .group_by(Feedback.trace_id)
    )

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    )

    rows = (
        await session.execute(
            base.order_by(func.max(Feedback.created_at).desc(), Feedback.trace_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if not rows:
        return FailureInboxPage(items=[], total=total or 0)

    order = [row.trace_id for row in rows]
    latest_by_trace = {row.trace_id: row.latest for row in rows}

    traces = {
        t.id: t
        for t in (
            await session.execute(select(Trace).where(Trace.id.in_(order)))
        ).scalars()
    }
    events: dict[uuid.UUID, list[Feedback]] = {}
    for entry in (
        await session.execute(
            select(Feedback)
            .where(Feedback.trace_id.in_(order), Feedback.rating < 0)
            .order_by(Feedback.created_at.desc())
        )
    ).scalars():
        events.setdefault(entry.trace_id, []).append(entry)

    # Real tallies, including upvotes: a trace that got both a 👍 and a 👎 is
    # exactly the ambiguous case a human triaging the inbox needs to see.
    counts = await feedback_counts(session, order)

    items = []
    for trace_id in order:
        trace = traces.get(trace_id)
        if trace is None:
            # A retention sweep between the two queries. Skipping beats 500ing;
            # the count is one row stale for one request.
            continue
        tally = counts.get(trace_id)
        items.append(
            FailureInboxItem(
                trace=summary_with_feedback(
                    trace,
                    negative=tally.negative if tally else 0,
                    positive=tally.positive if tally else 0,
                ),
                feedback=[
                    FeedbackOut.model_validate(e) for e in events.get(trace_id, [])
                ],
                latest_feedback_at=latest_by_trace[trace_id],
            )
        )

    return FailureInboxPage(items=items, total=total or 0)
