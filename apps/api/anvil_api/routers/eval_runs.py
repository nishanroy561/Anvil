"""Eval run APIs and the job-queue producer (plan/evaluation.md §3).

Spend guards live here, on the write path, because judge calls burn real
credits: a mistyped loop that creates runs must hit a wall at the API, not
after the worker has already spent the money.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..config import get_settings
from ..db import get_session
from ..deps import NOT_FOUND, owned_project
from ..models import Dataset, DatasetItem, EvalResult, EvalRun, User
from ..redis_client import get_redis
from ..schemas.evaluation import (
    EvalResultOut,
    EvalResultPage,
    EvalRunCreate,
    EvalRunOut,
    GraderInfo,
    RunComparison,
)
from ..services.analysis import compare_runs
from ..services.graders_catalog import builtin_graders, builtin_names
from ..streams import EVAL_JOBS_STREAM

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["evals"])

# A run in one of these is occupying a concurrency slot.
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


@router.get("/graders", response_model=list[GraderInfo])
async def list_graders(user: User = Depends(current_user)):
    """Registry-driven picker data (plan/plugins.md §2). Built-ins only — the
    API can't enumerate plugins installed in the worker image."""
    return [GraderInfo(**g) for g in builtin_graders()]


async def owned_run(run_id: uuid.UUID, user: User, session: AsyncSession) -> EvalRun:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise NOT_FOUND
    await owned_project(run.project_id, user, session)
    return run


@router.post("/eval-runs", response_model=EvalRunOut, status_code=201)
async def create_eval_run(
    body: EvalRunCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()

    dataset = await session.get(Dataset, body.dataset_id)
    if dataset is None:
        raise NOT_FOUND
    project = await owned_project(dataset.project_id, user, session)

    item_count = await session.scalar(
        select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
    ) or 0
    if item_count == 0:
        raise HTTPException(
            400,
            detail={"code": "empty_dataset", "message": "This dataset has no items to score."},
        )
    # Spend guard 1: bound the size of any single run.
    if item_count > settings.max_eval_items:
        raise HTTPException(
            400,
            detail={
                "code": "dataset_too_large",
                "message": (
                    f"This dataset has {item_count} items; the limit per run is "
                    f"{settings.max_eval_items}."
                ),
            },
        )

    # Spend guard 2: bound how many runs a project can have in flight.
    active = await session.scalar(
        select(func.count())
        .select_from(EvalRun)
        .where(EvalRun.project_id == project.id, EvalRun.status.in_(ACTIVE_STATUSES))
    ) or 0
    if active >= settings.max_concurrent_runs:
        raise HTTPException(
            429,
            detail={
                "code": "too_many_runs",
                "message": (
                    f"{active} runs are already queued or running (limit "
                    f"{settings.max_concurrent_runs}). Wait for one to finish."
                ),
            },
        )

    unknown = [g.name for g in body.graders if g.name not in builtin_names()]
    if unknown:
        # Not an error: the worker may have plugins the API can't see.
        log.info("eval run requests non-builtin graders %s (plugin?)", unknown)

    if body.target.mode == "generate" and not body.target.model:
        raise HTTPException(
            400,
            detail={
                "code": "target_model_required",
                "message": "Generate mode needs a target model. Use rescore mode to grade existing outputs.",
            },
        )

    run = EvalRun(
        project_id=project.id,
        dataset_id=dataset.id,
        name=body.name,
        status="queued",
        target_config=body.target.model_dump(),
        grader_config={"graders": [g.model_dump() for g in body.graders]},
        progress=0,
        total=item_count,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        await get_redis().xadd(EVAL_JOBS_STREAM, {"run_id": str(run.id)})
    except RedisError:
        # The row exists and is queued; a runner sweep will pick it up even if
        # the enqueue was lost, so this is degraded-but-correct, not a failure.
        log.exception("failed to enqueue eval run %s; leaving it queued for sweep", run.id)

    return EvalRunOut.model_validate(run)


@router.get("/projects/{project_id}/eval-runs", response_model=list[EvalRunOut])
async def list_eval_runs(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, user, session)
    filters = [EvalRun.project_id == project.id]
    if dataset_id is not None:
        filters.append(EvalRun.dataset_id == dataset_id)
    rows = await session.execute(
        select(EvalRun).where(*filters).order_by(EvalRun.created_at.desc()).limit(limit)
    )
    return [EvalRunOut.model_validate(r) for r in rows.scalars()]


@router.get("/eval-runs/compare", response_model=RunComparison)
async def compare_eval_runs(
    a: uuid.UUID = Query(..., description="Baseline run"),
    b: uuid.UUID = Query(..., description="Run being judged against the baseline"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Paired comparison of two runs over the same dataset (ADR-0008).

    Declared **before** `/eval-runs/{run_id}`: FastAPI matches routes in
    declaration order, and the other way round "compare" is parsed as a run id
    and 422s.
    """
    run_a = await owned_run(a, user, session)
    run_b = await owned_run(b, user, session)

    if run_a.dataset_id != run_b.dataset_id:
        raise HTTPException(
            400,
            detail={
                "code": "datasets_differ",
                "message": (
                    "These runs scored different datasets, so their items can't be "
                    "paired. Compare runs over the same dataset."
                ),
            },
        )

    async def _rows(run: EvalRun) -> list[dict]:
        result = await session.execute(select(EvalResult).where(EvalResult.run_id == run.id))
        return [
            {
                "item_id": r.item_id,
                "scores": r.scores,
                "latency_ms": r.latency_ms,
                "cost_usd": r.cost_usd,
            }
            for r in result.scalars()
        ]

    rows_a, rows_b = await _rows(run_a), await _rows(run_b)

    comparison = compare_runs(
        {
            "id": run_a.id,
            "name": run_a.name,
            "item_ids": run_a.item_ids,
            "grader_config": run_a.grader_config,
        },
        {
            "id": run_b.id,
            "name": run_b.name,
            "item_ids": run_b.item_ids,
            "grader_config": run_b.grader_config,
        },
        rows_a,
        rows_b,
    )
    return RunComparison(**comparison)


@router.get("/eval-runs/{run_id}", response_model=EvalRunOut)
async def get_eval_run(
    run_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return EvalRunOut.model_validate(await owned_run(run_id, user, session))


@router.get("/eval-runs/{run_id}/results", response_model=EvalResultPage)
async def get_eval_results(
    run_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    run = await owned_run(run_id, user, session)
    total = await session.scalar(
        select(func.count()).select_from(EvalResult).where(EvalResult.run_id == run.id)
    )
    rows = await session.execute(
        select(EvalResult)
        .where(EvalResult.run_id == run.id)
        .order_by(EvalResult.created_at, EvalResult.id)
        .limit(limit)
        .offset(offset)
    )
    return EvalResultPage(
        items=[EvalResultOut.model_validate(r) for r in rows.scalars()],
        total=total or 0,
    )


@router.post("/eval-runs/{run_id}/cancel", response_model=EvalRunOut)
async def cancel_eval_run(
    run_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cooperative cancellation: the runner checks status between items, so a
    cancel lands within one item rather than mid-call."""
    run = await owned_run(run_id, user, session)
    if run.status in TERMINAL_STATUSES:
        raise HTTPException(
            409,
            detail={
                "code": "run_not_cancellable",
                "message": f"This run already {run.status}.",
            },
        )
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)
    return EvalRunOut.model_validate(run)
