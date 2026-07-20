"""Derived fields computed at persist (plan/ingestion.md §4).

No services needed — these are pure functions, and they are what the dashboard's
cost and search columns are made of.
"""

import uuid

from anvil_worker.derive import (
    SEARCH_TEXT_MAX,
    build_search_text,
    derive_trace_fields,
    span_cost_usd,
    span_model,
)
from anvil_worker.pricing import PRICES_PER_MTOK

from .factories import make_span, make_trace, utc


def test_cost_uses_the_built_in_price_table():
    price_in, price_out = PRICES_PER_MTOK["claude-sonnet-5"]
    cost = span_cost_usd("claude-sonnet-5", 1_000_000, 1_000_000)
    assert cost == round(price_in + price_out, 6)


def test_cost_is_case_insensitive_and_prorated():
    cost = span_cost_usd("CLAUDE-SONNET-5", 1000, 0)
    assert cost == round(PRICES_PER_MTOK["claude-sonnet-5"][0] * 1000 / 1_000_000, 6)


def test_unknown_model_costs_null_not_an_error():
    """Self-hosted and custom models are normal; a missing price must never
    fail ingestion."""
    assert span_cost_usd("some-local-llama", 100, 100) is None
    assert span_cost_usd(None, 100, 100) is None


def test_span_model_falls_back_to_the_trace_model():
    assert span_model({"attributes": {"model": "gpt-4o"}}, "claude-sonnet-5") == "gpt-4o"
    assert span_model({"attributes": {}}, "claude-sonnet-5") == "claude-sonnet-5"
    assert span_model({}, None) is None


def test_totals_roll_up_from_spans():
    trace = make_trace(
        uuid.uuid4(),
        spans=[
            make_span(tokens_in=100, tokens_out=50),
            make_span(tokens_in=200, tokens_out=25),
            make_span(type="retrieval", tokens_in=None, tokens_out=None),
        ],
    )
    derive_trace_fields(trace)

    assert trace["total_tokens_in"] == 300
    assert trace["total_tokens_out"] == 75
    assert trace["total_cost_usd"] > 0
    # Only llm spans are priced; a retrieval span has no per-token cost.
    assert trace["spans"][2]["cost_usd"] is None


def test_totals_are_null_when_nothing_reported_them():
    """Null means "not measured"; zero would claim a free request."""
    trace = make_trace(
        uuid.uuid4(),
        model=None,
        spans=[make_span(type="tool", tokens_in=None, tokens_out=None)],
    )
    derive_trace_fields(trace)

    assert trace["total_tokens_in"] is None
    assert trace["total_cost_usd"] is None


def test_latency_is_derived_and_never_negative():
    trace = make_trace(uuid.uuid4(), started_at=utc(0), ended_at=utc(2))
    derive_trace_fields(trace)
    assert 1900 <= trace["latency_ms"] <= 2100

    unfinished = make_trace(uuid.uuid4(), ended_at=None)
    derive_trace_fields(unfinished)
    assert unfinished["latency_ms"] is None


def test_search_text_covers_name_and_root_payloads_only():
    trace = make_trace(
        uuid.uuid4(),
        name="Support Chat",
        input={"question": "How do I RESET my password"},
        output={"answer": "Use the reset link"},
        spans=[make_span(input={"prompt": "secret span payload"})],
    )
    text = build_search_text(trace)

    assert "support chat" in text
    assert "reset my password" in text
    assert "reset link" in text
    # Span payloads are deliberately not searchable (ADR-0004/0005).
    assert "secret span payload" not in text


def test_search_text_is_capped():
    trace = make_trace(uuid.uuid4(), input={"blob": "x" * 50_000})
    assert len(build_search_text(trace)) <= SEARCH_TEXT_MAX
