"""Input hygiene at ingest (checklist rows 6 and 7). No services needed.

The governing rule: repair and flag, never reject. A batch dropped because one
field was oversized or one clock was wrong loses observability data exactly when
the customer's system is misbehaving.
"""

from datetime import datetime, timedelta, timezone

from anvil_api.services.normalize import (
    CLOCK_ADJUSTED_FLAG,
    ORIGINAL_BYTES,
    TRUNCATED_FLAG,
    normalize_trace,
)

MAX_FIELD = 1024


def utc(offset_s: float = 0) -> str:
    return (datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)).isoformat()


def _trace(**overrides) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "trace",
        "started_at": utc(0),
        "ended_at": utc(10),
        "spans": [],
    }
    base.update(overrides)
    return base


# ── oversized payloads (row 6) ──────────────────────────────────────────

def test_oversized_span_field_is_truncated_and_flagged():
    span = {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "llm",
        "type": "llm",
        "started_at": utc(1),
        "input": {"prompt": "x" * 5000},
    }
    trace = normalize_trace(_trace(spans=[span]), MAX_FIELD)

    assert span["input"][TRUNCATED_FLAG] is True
    assert span["input"][ORIGINAL_BYTES] > MAX_FIELD
    assert "preview" in span["input"]  # enough to recognize it in the waterfall
    assert span["attributes"][TRUNCATED_FLAG] is True
    assert trace["_anvil_stats"]["truncated_fields"] == 1


def test_oversized_trace_field_marks_metadata():
    trace = normalize_trace(_trace(output={"answer": "y" * 5000}), MAX_FIELD)
    assert trace["output"][TRUNCATED_FLAG] is True
    assert trace["metadata"][TRUNCATED_FLAG] is True


def test_fields_within_the_limit_are_untouched():
    trace = normalize_trace(_trace(input={"q": "short"}), MAX_FIELD)
    assert trace["input"] == {"q": "short"}
    assert trace["_anvil_stats"]["truncated_fields"] == 0


# ── clock skew and negative durations (row 7) ───────────────────────────

def test_trace_ending_before_it_starts_is_clamped_to_zero_duration():
    trace = normalize_trace(_trace(started_at=utc(10), ended_at=utc(0)), MAX_FIELD)
    assert trace["ended_at"] == trace["started_at"]
    assert trace["metadata"][CLOCK_ADJUSTED_FLAG] is True


def test_span_outside_the_trace_window_is_clamped_to_its_bounds():
    early = {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "early", "type": "tool",
        "started_at": utc(-500), "ended_at": utc(5),
    }
    late = {
        "id": "44444444-4444-4444-4444-444444444444",
        "name": "late", "type": "tool",
        "started_at": utc(2), "ended_at": utc(9999),
    }
    trace = normalize_trace(_trace(spans=[early, late]), MAX_FIELD)

    assert early["started_at"] == trace["started_at"]
    assert late["ended_at"] == trace["ended_at"]
    assert early["attributes"][CLOCK_ADJUSTED_FLAG] is True
    assert late["attributes"][CLOCK_ADJUSTED_FLAG] is True
    assert trace["_anvil_stats"]["clock_adjusted_spans"] == 2


def test_negative_span_duration_is_clamped():
    span = {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "backwards", "type": "llm",
        "started_at": utc(5), "ended_at": utc(1),
    }
    normalize_trace(_trace(spans=[span]), MAX_FIELD)
    assert span["ended_at"] == span["started_at"]


def test_in_progress_trace_does_not_clamp_spans_to_a_missing_end():
    """An unfinished trace has no upper bound; spans must not be dragged to one."""
    span = {
        "id": "66666666-6666-6666-6666-666666666666",
        "name": "running", "type": "llm",
        "started_at": utc(3), "ended_at": utc(8),
    }
    normalize_trace(_trace(ended_at=None, spans=[span]), MAX_FIELD)
    assert span["started_at"] == utc(3)
    assert span["ended_at"] == utc(8)


# ── malformed span trees (row 7) ────────────────────────────────────────

def _span(span_id: str, parent: str | None = None) -> dict:
    return {
        "id": span_id, "name": "s", "type": "custom",
        "parent_span_id": parent, "started_at": utc(1), "ended_at": utc(2),
    }


A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MISSING = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def test_dangling_parent_becomes_a_root_not_an_error():
    orphan = _span(A, parent=MISSING)
    trace = normalize_trace(_trace(spans=[orphan]), MAX_FIELD)
    assert orphan["parent_span_id"] is None
    assert trace["_anvil_stats"]["repaired_parents"] == 1


def test_self_parenting_span_is_repaired():
    span = _span(A, parent=A)
    normalize_trace(_trace(spans=[span]), MAX_FIELD)
    assert span["parent_span_id"] is None


def test_cyclic_parents_are_broken():
    """A → B → A would hang any renderer that walks to a root."""
    first, second = _span(A, parent=B), _span(B, parent=A)
    normalize_trace(_trace(spans=[first, second]), MAX_FIELD)
    assert first["parent_span_id"] is None or second["parent_span_id"] is None


def test_valid_nesting_is_preserved():
    root, child, grandchild = _span(A), _span(B, parent=A), _span(C, parent=B)
    normalize_trace(_trace(spans=[root, child, grandchild]), MAX_FIELD)
    assert root["parent_span_id"] is None
    assert child["parent_span_id"] == A
    assert grandchild["parent_span_id"] == B
