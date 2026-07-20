"""Derived fields, computed once at persist and never at query time
(plan/ingestion.md §4).

The dashboard reads these columns directly. Computing cost or search text at
query time would make every list request pay for work that changes only when
the trace is written.
"""

import json
from datetime import datetime
from typing import Any

from .registry import resolve_price

SEARCH_TEXT_MAX = 4096
TOKENS_PER_MILLION = 1_000_000


def parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def span_cost_usd(
    model: str | None, tokens_in: int | None, tokens_out: int | None
) -> float | None:
    """Unknown model -> None. A missing price is normal (self-hosted and custom
    models), never an ingestion error."""
    price = resolve_price(model)
    if price is None:
        return None
    price_in, price_out = price
    return round(
        (tokens_in or 0) * price_in / TOKENS_PER_MILLION
        + (tokens_out or 0) * price_out / TOKENS_PER_MILLION,
        6,
    )


def span_model(span: dict, trace_model: str | None) -> str | None:
    """Spans carry their model in attributes; fall back to the trace's model so
    a single-model trace doesn't need it repeated on every span."""
    attributes = span.get("attributes")
    if isinstance(attributes, dict):
        model = attributes.get("model")
        if isinstance(model, str) and model:
            return model
    return trace_model


def _flatten_text(value: Any, budget: int) -> str:
    """Text worth searching, from arbitrary client JSON."""
    if value is None or budget <= 0:
        return ""
    if isinstance(value, str):
        return value[:budget]
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if budget <= 0:
                break
            chunk = _flatten_text(item, budget)
            if chunk:
                parts.append(f"{key} {chunk}")
                budget -= len(chunk)
        return " ".join(parts)
    if isinstance(value, list):
        parts = []
        for item in value:
            if budget <= 0:
                break
            chunk = _flatten_text(item, budget)
            if chunk:
                parts.append(chunk)
                budget -= len(chunk)
        return " ".join(parts)
    return json.dumps(value, default=str)[:budget]


def build_search_text(trace: dict) -> str:
    """Trace name + root input/output, lowercased and capped.

    Span payloads are deliberately excluded (ADR-0004/0005): indexing them would
    multiply index size by the span count for search hits nobody asks for.
    """
    parts = [
        str(trace.get("name") or ""),
        _flatten_text(trace.get("input"), SEARCH_TEXT_MAX),
        _flatten_text(trace.get("output"), SEARCH_TEXT_MAX),
    ]
    return " ".join(p for p in parts if p).lower()[:SEARCH_TEXT_MAX]


def derive_trace_fields(trace: dict) -> dict:
    """Rolls span costs/tokens up onto the trace and computes latency.

    Returns the trace dict with derived keys added and each span's `cost_usd`
    filled in.
    """
    spans = trace.get("spans") or []
    trace_model = trace.get("model")

    total_in = total_out = 0
    total_cost = 0.0
    saw_tokens = saw_cost = False

    for span in spans:
        tokens_in, tokens_out = span.get("tokens_in"), span.get("tokens_out")
        if tokens_in or tokens_out:
            saw_tokens = True
            total_in += tokens_in or 0
            total_out += tokens_out or 0

        cost = None
        if span.get("type") == "llm":
            cost = span_cost_usd(span_model(span, trace_model), tokens_in, tokens_out)
        span["cost_usd"] = cost
        if cost is not None:
            saw_cost = True
            total_cost += cost

    started_at = parse_ts(trace.get("started_at"))
    ended_at = parse_ts(trace.get("ended_at"))
    latency_ms = None
    if started_at and ended_at:
        latency_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

    trace["total_tokens_in"] = total_in if saw_tokens else None
    trace["total_tokens_out"] = total_out if saw_tokens else None
    trace["total_cost_usd"] = round(total_cost, 6) if saw_cost else None
    trace["latency_ms"] = latency_ms
    trace["search_text"] = build_search_text(trace)
    return trace
