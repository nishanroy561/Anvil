"""Trace payload builders for tests — the shape the API puts on the stream."""

import uuid
from datetime import datetime, timedelta, timezone


def utc(offset_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


# `ended_at=None` must mean "still running", which is distinct from "not
# specified, use a default" — an in_progress trace with an end time would make
# the monotonic-guard tests silently vacuous.
UNSET = object()


def make_span(
    *,
    name: str = "llm call",
    type: str = "llm",
    parent_span_id: str | None = None,
    tokens_in: int | None = 100,
    tokens_out: int | None = 50,
    started_at: str | None = None,
    ended_at: str | None = None,
    **extra,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "type": type,
        "parent_span_id": parent_span_id,
        "status": "ok",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "started_at": started_at or utc(0),
        "ended_at": ended_at or utc(1),
        **extra,
    }


def make_trace(
    project_id: uuid.UUID | str,
    *,
    trace_id: str | None = None,
    name: str = "rag query",
    status: str = "ok",
    spans: list[dict] | None = None,
    started_at: str | None = None,
    ended_at=UNSET,
    model: str | None = "claude-sonnet-5",
    **extra,
) -> dict:
    """A stream payload: post-normalization, with project_id already stamped
    from the API key."""
    return {
        "id": trace_id or str(uuid.uuid4()),
        "project_id": str(project_id),
        "name": name,
        "status": status,
        "model": model,
        "input": {"question": "what is anvil"},
        "output": {"answer": "an llm observability platform"},
        "metadata": {"env": "test"},
        "started_at": started_at or utc(0),
        "ended_at": utc(2) if ended_at is UNSET else ended_at,
        "spans": spans if spans is not None else [make_span()],
        **extra,
    }
