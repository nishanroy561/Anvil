import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- datasets --------------------------------------------------------------


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None


class DatasetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    item_count: int = 0

    model_config = {"from_attributes": True}


class DatasetItemIn(BaseModel):
    input: Any | None = None
    expected_output: Any | None = None
    metadata: dict | None = None


class DatasetItemsCreate(BaseModel):
    """Manual add and bulk upload are the same call — one item is a list of one."""

    items: list[DatasetItemIn] = Field(min_length=1, max_length=1000)


class DatasetItemUpdate(BaseModel):
    """Editable expected outputs are the point of the items table (spec §1)."""

    input: Any | None = None
    expected_output: Any | None = None
    metadata: dict | None = None


class DatasetItemOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    input: Any | None
    expected_output: Any | None
    metadata: dict | None = None
    source_trace_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class DatasetItemPage(BaseModel):
    items: list[DatasetItemOut]
    total: int


class PromoteRequest(BaseModel):
    trace_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class PromoteResult(BaseModel):
    promoted: int
    skipped: int  # traces not found in this project, or already promoted here


# ---- eval runs -------------------------------------------------------------


class TargetConfig(BaseModel):
    """How to produce the output being graded.

    `mode="rescore"` grades whatever the item already carries (the promoted
    trace's output), so a run needs no model credentials at all.
    """

    mode: Literal["generate", "rescore"] = "generate"
    model: str | None = None
    prompt: str | None = None  # supports {{variable}} filled from item input
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32_000)


class GraderConfig(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    config: dict = Field(default_factory=dict)


class EvalRunCreate(BaseModel):
    dataset_id: uuid.UUID
    name: str = Field(min_length=1, max_length=256)
    target: TargetConfig = Field(default_factory=TargetConfig)
    graders: list[GraderConfig] = Field(min_length=1, max_length=20)


class EvalRunOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    status: str
    target_config: dict | None
    grader_config: dict | None
    progress: int
    total: int
    summary: dict | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class EvalResultOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    item_id: uuid.UUID
    output: Any | None
    scores: dict | None
    judge_reasoning: dict | None
    latency_ms: int | None
    cost_usd: float | None
    error: str | None

    model_config = {"from_attributes": True}


class EvalResultPage(BaseModel):
    items: list[EvalResultOut]
    total: int


class GraderInfo(BaseModel):
    """Drives the UI's grader picker — the registry is the source of truth, so
    an installed plugin appears without a frontend change (plan/plugins.md §2)."""

    name: str
    kind: Literal["deterministic", "judge"]
    description: str
    config_schema: dict
