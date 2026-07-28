"""Monitor and alert schemas (plan/evaluation.md §6)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Kept in step with `anvil_worker/alerter.py`. The worker is the implementation;
# these bound what the API will accept so a rule that can never be evaluated
# cannot be created in the first place.
Metric = Literal["error_rate", "cost", "latency_p95", "eval_score"]
Comparison = Literal["gt", "lt", "gte", "lte"]


class GraderSpec(BaseModel):
    name: str
    config: dict = Field(default_factory=dict)


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    # 0 is allowed and means "sample nothing" — a way to pause a monitor
    # without losing its configuration.
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    schedule_cron: str = Field(default="*/10 * * * *", max_length=64)
    graders: list[GraderSpec] = Field(default_factory=list)
    enabled: bool = True


class MonitorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    sample_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    schedule_cron: str | None = Field(default=None, max_length=64)
    graders: list[GraderSpec] | None = None
    enabled: bool | None = None


class MonitorOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    sample_rate: float
    schedule_cron: str
    grader_config: dict | None
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MonitorScoreOut(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID
    trace_id: uuid.UUID
    scores: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScoreBucket(BaseModel):
    """One point on the drift chart."""

    bucket: datetime
    mean_score: float | None
    samples: int


class MonitorScoreSeries(BaseModel):
    buckets: list[ScoreBucket]
    # Per grader, so a chart can show which check is sliding rather than only
    # that "quality" moved.
    graders: list[str]


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    metric: Metric
    comparison: Comparison
    threshold: float
    window_minutes: int = Field(default=15, ge=1, le=10_080)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    metric: Metric | None = None
    comparison: Comparison | None = None
    threshold: float | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=10_080)
    enabled: bool | None = None


class AlertRuleOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    metric: str
    comparison: str
    threshold: float
    window_minutes: int
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertEventOut(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str | None = None
    metric: str | None = None
    fired_at: datetime
    value: float
    resolved_at: datetime | None
    # False means the alert fired but a channel could not be reached — a
    # degraded state worth showing, not a silent success.
    notified: bool

    model_config = {"from_attributes": True}


class WebhookUpdate(BaseModel):
    # None clears it; the dashboard bell is always on regardless.
    alert_webhook_url: str | None = None
