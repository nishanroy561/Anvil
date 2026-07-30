"""Feedback wire shapes (plan/evaluation.md §7)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .traces import TraceSummary


class FeedbackCreate(BaseModel):
    """What an end user's thumbs-down looks like on the wire.

    `extra="forbid"` is doing the same job as `additionalProperties: false` in
    the trace schema: it makes `project_id` *unexpressible* in the body rather
    than merely ignored, so tenancy can only come from the resolved API key.

    `rating` is a two-value literal on purpose. A permissive `int` would accept
    `0` — neither thumbs up nor down — and a client bug sending it would file
    feedback that no filter ever surfaces and no inbox ever shows.
    """

    trace_id: uuid.UUID
    rating: Literal[-1, 1]
    comment: str | None = None
    # Who is voting, in the customer's own namespace. Present, this makes repeat
    # feedback an upsert (checklist row 13); absent, every submission is its own
    # anonymous row.
    user_ref: str | None = Field(None, max_length=256)

    model_config = {"extra": "forbid"}


class FeedbackAccepted(BaseModel):
    """202 body. `id` is minted here rather than in the worker so a redelivery
    of the same stream entry upserts one row even when `user_ref` is absent and
    the partial unique index does not apply."""

    id: uuid.UUID
    accepted: bool = True


class FeedbackOut(BaseModel):
    id: uuid.UUID
    trace_id: uuid.UUID
    rating: int
    comment: str | None
    user_ref: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FailureInboxItem(BaseModel):
    """A thumbs-down trace that is not yet in any dataset — i.e. a real failure
    nobody has turned into a test case."""

    trace: TraceSummary
    feedback: list[FeedbackOut]
    latest_feedback_at: datetime


class FailureInboxPage(BaseModel):
    items: list[FailureInboxItem]
    total: int
