"""Dataset CRUD, items, and trace promotion (plan/evaluation.md §1). Session auth.

Promotion copies a trace's root input/output into an item rather than
referencing it: `dataset_items.source_trace_id` is ON DELETE SET NULL, so
retention deleting the source trace leaves the dataset intact (checklist
row 24).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..db import get_session
from ..deps import NOT_FOUND, owned_project
from ..models import Dataset, DatasetItem, EvalRun, Trace, User
from ..schemas.evaluation import (
    DatasetCreate,
    DatasetItemOut,
    DatasetItemPage,
    DatasetItemsCreate,
    DatasetItemUpdate,
    DatasetOut,
    DatasetUpdate,
    PromoteRequest,
    PromoteResult,
)

router = APIRouter(prefix="/v1", tags=["datasets"])


async def owned_dataset(dataset_id: uuid.UUID, user: User, session: AsyncSession) -> Dataset:
    """A dataset in another org 404s exactly like one that doesn't exist."""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NOT_FOUND
    await owned_project(dataset.project_id, user, session)
    return dataset


async def _item_counts(session: AsyncSession, dataset_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not dataset_ids:
        return {}
    rows = await session.execute(
        select(DatasetItem.dataset_id, func.count())
        .where(DatasetItem.dataset_id.in_(dataset_ids))
        .group_by(DatasetItem.dataset_id)
    )
    return {dataset_id: count for dataset_id, count in rows}


def _out(dataset: Dataset, item_count: int) -> DatasetOut:
    return DatasetOut(
        id=dataset.id,
        project_id=dataset.project_id,
        name=dataset.name,
        description=dataset.description,
        created_at=dataset.created_at,
        item_count=item_count,
    )


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut])
async def list_datasets(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, user, session)
    rows = await session.execute(
        select(Dataset)
        .where(Dataset.project_id == project.id)
        .order_by(Dataset.created_at.desc())
    )
    datasets = list(rows.scalars())
    counts = await _item_counts(session, [d.id for d in datasets])
    return [_out(d, counts.get(d.id, 0)) for d in datasets]


@router.post("/projects/{project_id}/datasets", response_model=DatasetOut, status_code=201)
async def create_dataset(
    project_id: uuid.UUID,
    body: DatasetCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, user, session)
    dataset = Dataset(project_id=project.id, name=body.name, description=body.description)
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return _out(dataset, 0)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
async def get_dataset(
    dataset_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    dataset = await owned_dataset(dataset_id, user, session)
    counts = await _item_counts(session, [dataset.id])
    return _out(dataset, counts.get(dataset.id, 0))


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
async def update_dataset(
    dataset_id: uuid.UUID,
    body: DatasetUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    dataset = await owned_dataset(dataset_id, user, session)
    if body.name is not None:
        dataset.name = body.name
    if body.description is not None:
        dataset.description = body.description
    await session.commit()
    await session.refresh(dataset)
    counts = await _item_counts(session, [dataset.id])
    return _out(dataset, counts.get(dataset.id, 0))


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    dataset = await owned_dataset(dataset_id, user, session)
    # Eval runs reference the dataset and their results reference its items;
    # deleting underneath them would strand history rather than free anything.
    existing_run = await session.execute(
        select(EvalRun.id).where(EvalRun.dataset_id == dataset.id).limit(1)
    )
    if existing_run.scalar_one_or_none() is not None:
        raise HTTPException(
            409,
            detail={
                "code": "dataset_in_use",
                "message": "This dataset has eval runs. Delete those runs first.",
            },
        )
    await session.execute(delete(DatasetItem).where(DatasetItem.dataset_id == dataset.id))
    await session.delete(dataset)
    await session.commit()


# ---- items -----------------------------------------------------------------


@router.get("/datasets/{dataset_id}/items", response_model=DatasetItemPage)
async def list_items(
    dataset_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    dataset = await owned_dataset(dataset_id, user, session)
    total = await session.scalar(
        select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
    )
    rows = await session.execute(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset.id)
        .order_by(DatasetItem.id)
        .limit(limit)
        .offset(offset)
    )
    return DatasetItemPage(
        items=[
            DatasetItemOut(
                id=i.id,
                dataset_id=i.dataset_id,
                input=i.input,
                expected_output=i.expected_output,
                metadata=i.meta,
                source_trace_id=i.source_trace_id,
            )
            for i in rows.scalars()
        ],
        total=total or 0,
    )


@router.post("/datasets/{dataset_id}/items", response_model=list[DatasetItemOut], status_code=201)
async def add_items(
    dataset_id: uuid.UUID,
    body: DatasetItemsCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    dataset = await owned_dataset(dataset_id, user, session)
    items = [
        DatasetItem(
            dataset_id=dataset.id,
            input=item.input,
            expected_output=item.expected_output,
            meta=item.metadata,
        )
        for item in body.items
    ]
    session.add_all(items)
    await session.commit()
    for item in items:
        await session.refresh(item)
    return [
        DatasetItemOut(
            id=i.id,
            dataset_id=i.dataset_id,
            input=i.input,
            expected_output=i.expected_output,
            metadata=i.meta,
            source_trace_id=i.source_trace_id,
        )
        for i in items
    ]


@router.patch("/dataset-items/{item_id}", response_model=DatasetItemOut)
async def update_item(
    item_id: uuid.UUID,
    body: DatasetItemUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(DatasetItem, item_id)
    if item is None:
        raise NOT_FOUND
    await owned_dataset(item.dataset_id, user, session)

    fields = body.model_dump(exclude_unset=True)
    if "input" in fields:
        item.input = fields["input"]
    if "metadata" in fields:
        item.meta = fields["metadata"]
    if "expected_output" in fields:
        item.expected_output = fields["expected_output"]
        # Applied after `metadata` so an explicit metadata write can't undo it,
        # and reassigned rather than mutated so SQLAlchemy sees the JSONB change.
        # This is what tells a rescore run the reference is now a real reference
        # and not just a copy of the output (ADR-0018).
        item.meta = {**(item.meta or {}), "expected_output_edited": True}
    await session.commit()
    await session.refresh(item)
    return DatasetItemOut(
        id=item.id,
        dataset_id=item.dataset_id,
        input=item.input,
        expected_output=item.expected_output,
        metadata=item.meta,
        source_trace_id=item.source_trace_id,
    )


@router.delete("/dataset-items/{item_id}", status_code=204)
async def delete_item(
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(DatasetItem, item_id)
    if item is None:
        raise NOT_FOUND
    await owned_dataset(item.dataset_id, user, session)
    await session.delete(item)
    await session.commit()


# ---- promotion -------------------------------------------------------------


@router.post("/datasets/{dataset_id}/promote", response_model=PromoteResult)
async def promote_traces(
    dataset_id: uuid.UUID,
    body: PromoteRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Copies each trace's root input/output into an item.

    Traces outside the dataset's project are silently skipped rather than
    404ing the whole batch — a bulk select in the UI shouldn't fail wholesale
    because one row was stale. Re-promoting a trace already in this dataset is
    a no-op, so the button is safe to double-click.
    """
    dataset = await owned_dataset(dataset_id, user, session)

    rows = await session.execute(
        select(Trace).where(
            Trace.id.in_(body.trace_ids), Trace.project_id == dataset.project_id
        )
    )
    traces = list(rows.scalars())

    already = await session.execute(
        select(DatasetItem.source_trace_id).where(
            DatasetItem.dataset_id == dataset.id,
            DatasetItem.source_trace_id.in_([t.id for t in traces] or [uuid.uuid4()]),
        )
    )
    seen = {row for row in already.scalars() if row is not None}

    promoted = 0
    for trace in traces:
        if trace.id in seen:
            continue
        session.add(
            DatasetItem(
                dataset_id=dataset.id,
                input=trace.input,
                expected_output=trace.output,
                meta={
                    # Customer metadata first: Anvil's own keys must win, or a
                    # trace carrying `recorded_output` in its metadata could
                    # shadow the real one.
                    **(trace.meta or {}),
                    "promoted_from_trace": str(trace.id),
                    "trace_name": trace.name,
                    # What the app actually produced, kept apart from the
                    # reference it will be graded against (ADR-0018). Both start
                    # equal; editing `expected_output` is what makes the pair
                    # meaningful, and `expected_output_edited` records that.
                    "recorded_output": trace.output,
                    "expected_output_edited": False,
                },
                source_trace_id=trace.id,
            )
        )
        promoted += 1

    await session.commit()
    return PromoteResult(promoted=promoted, skipped=len(body.trace_ids) - promoted)
