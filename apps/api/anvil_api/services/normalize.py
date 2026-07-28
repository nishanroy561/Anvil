"""Input hygiene for ingested traces (plan/ingestion.md §1).

Client data is untrusted: payloads can be huge, clocks can be wrong, and span
trees can be malformed. Every problem here is *repaired and flagged*, never
rejected — a batch dropped for one oversized field loses observability data
precisely when something is going wrong. Rejection is reserved for payloads
that fail the wire schema outright.

Covers edge-case checklist rows 6 (oversized payloads) and 7 (clock skew,
negative durations, dangling parents).
"""

import json
from datetime import datetime
from typing import Any

TRUNCATED_FLAG = "anvil_truncated"
ORIGINAL_BYTES = "original_bytes"
CLOCK_ADJUSTED_FLAG = "anvil_clock_adjusted"
# Set when a span's parent link was nulled below. Without it the repair is
# lossy in a way that matters: a span whose parent never arrived becomes
# indistinguishable from a genuine root, and the dashboard cannot tell the
# operator that part of their trace is missing.
ORPHANED_FLAG = "anvil_orphaned"

# How much of an oversized field we keep. Enough to recognize the payload in the
# waterfall without storing the multi-MB blob that tripped the limit.
PREVIEW_CHARS = 2048


def _byte_len(value: Any) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _truncate_field(value: Any, max_bytes: int) -> tuple[Any, int | None]:
    """Returns (value, original_bytes) — original_bytes is None if it fit."""
    if value is None:
        return value, None
    size = _byte_len(value)
    if size <= max_bytes:
        return value, None
    preview = json.dumps(value, default=str)[:PREVIEW_CHARS]
    return {TRUNCATED_FLAG: True, ORIGINAL_BYTES: size, "preview": preview}, size


def _truncate_payload_fields(
    obj: dict, fields: tuple[str, ...], marker_target: str, max_bytes: int
) -> int:
    """Truncates oversized fields in place; records the marker on
    `marker_target` (span attributes / trace metadata). Returns fields cut."""
    truncated_bytes = 0
    cut = 0
    for field in fields:
        new_value, original = _truncate_field(obj.get(field), max_bytes)
        if original is not None:
            obj[field] = new_value
            truncated_bytes += original
            cut += 1
    if cut:
        marker = obj.get(marker_target) or {}
        if not isinstance(marker, dict):  # client sent a non-object; don't lose it
            marker = {"value": marker}
        marker[TRUNCATED_FLAG] = True
        # Spec keys are per-object, so with several oversized fields this is the
        # summed original size of everything cut on this object.
        marker[ORIGINAL_BYTES] = truncated_bytes
        obj[marker_target] = marker
    return cut


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _orphan(span: dict) -> None:
    """Nulls a span's parent link and flags that it once had one."""
    span["parent_span_id"] = None
    attributes = span.get("attributes") or {}
    if isinstance(attributes, dict):
        attributes[ORPHANED_FLAG] = True
        span["attributes"] = attributes


def _null_bad_parents(spans: list[dict]) -> int:
    """A `parent_span_id` that names no span in this trace, points at itself, or
    sits in a cycle is nulled — the span becomes root-level, not an error.

    The span is flagged as it is repaired. Nulling alone destroys the evidence:
    downstream, "my parent never arrived" and "I am a root" look identical, so
    the waterfall could never surface a dropped parent to the operator.
    """
    ids = {s.get("id") for s in spans}
    parent_of = {s.get("id"): s.get("parent_span_id") for s in spans}
    repaired = 0

    for span in spans:
        parent = span.get("parent_span_id")
        if parent is None:
            continue
        if parent not in ids or parent == span.get("id"):
            _orphan(span)
            repaired += 1
            continue
        # Walk to a root; if we revisit a node, this span sits in a cycle.
        seen = {span.get("id")}
        cursor = parent
        while cursor is not None:
            if cursor in seen:
                _orphan(span)
                repaired += 1
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor)
    return repaired


def normalize_trace(trace: dict, max_field_bytes: int) -> dict:
    """Repairs one schema-valid trace in place and returns it.

    Adds `_anvil_stats` — counters the router turns into metrics. The key is
    stripped before the trace is written to the stream.
    """
    spans: list[dict] = trace.get("spans") or []
    truncated = _truncate_payload_fields(
        trace, ("input", "output"), "metadata", max_field_bytes
    )
    for span in spans:
        truncated += _truncate_payload_fields(
            span, ("input", "output"), "attributes", max_field_bytes
        )

    # Clocks: a trace can't end before it starts, and a span can't fall outside
    # its trace's window. Clamp rather than reject; flag what we touched.
    trace_start = _parse_ts(trace.get("started_at"))
    trace_end = _parse_ts(trace.get("ended_at"))
    adjusted = 0

    if trace_start and trace_end and trace_end < trace_start:
        trace["ended_at"] = trace["started_at"]  # zero duration
        trace_end = trace_start
        metadata = trace.get("metadata") or {}
        if isinstance(metadata, dict):
            metadata[CLOCK_ADJUSTED_FLAG] = True
            trace["metadata"] = metadata
        adjusted += 1

    for span in spans:
        span_start = _parse_ts(span.get("started_at"))
        span_end = _parse_ts(span.get("ended_at"))
        touched = False

        if span_start and trace_start and span_start < trace_start:
            span["started_at"] = trace["started_at"]
            span_start = trace_start
            touched = True
        # Only clamp to the trace end when the trace actually has one; an
        # in-progress trace has no upper bound to clamp against.
        if span_start and trace_end and span_start > trace_end:
            span["started_at"] = trace["ended_at"]
            span_start = trace_end
            touched = True
        if span_end and span_start and span_end < span_start:
            span["ended_at"] = span["started_at"]
            span_end = span_start
            touched = True
        if span_end and trace_end and span_end > trace_end:
            span["ended_at"] = trace["ended_at"]
            touched = True

        if touched:
            attributes = span.get("attributes") or {}
            if isinstance(attributes, dict):
                attributes[CLOCK_ADJUSTED_FLAG] = True
                span["attributes"] = attributes
            adjusted += 1

    repaired_parents = _null_bad_parents(spans)

    trace["_anvil_stats"] = {
        "truncated_fields": truncated,
        "clock_adjusted_spans": adjusted,
        "repaired_parents": repaired_parents,
    }
    return trace
