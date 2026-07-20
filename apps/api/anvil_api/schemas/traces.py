import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SpanOut(BaseModel):
    id: uuid.UUID
    trace_id: uuid.UUID
    parent_span_id: uuid.UUID | None
    name: str
    type: str
    status: str
    input: Any | None
    output: Any | None
    attributes: dict | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    error: str | None
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class TraceSummary(BaseModel):
    """List-row shape: no payloads. Trace input/output are unbounded JSONB and
    would dominate the response; the detail endpoint serves them."""

    id: uuid.UUID
    name: str
    status: str
    model: str | None
    latency_ms: int | None
    total_tokens_in: int | None
    total_tokens_out: int | None
    total_cost_usd: float | None
    session_id: str | None
    user_ref: str | None
    metadata: dict | None
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class TraceListPage(BaseModel):
    items: list[TraceSummary]
    next_cursor: str | None  # null means the last page


class TraceDetail(TraceSummary):
    input: Any | None
    output: Any | None
    # Flat and ordered by start time. Nesting is expressed by parent_span_id and
    # assembled client-side: the waterfall must tolerate orphans anyway
    # (checklist row 7), so the server never fabricates a root to hang them on.
    spans: list[SpanOut]


class TimeBucket(BaseModel):
    bucket: datetime
    traces: int
    errors: int
    cost_usd: float
    tokens_in: int
    tokens_out: int


class MetricsOverview(BaseModel):
    total_traces: int
    error_rate: float
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    interval: str
    buckets: list[TimeBucket]
