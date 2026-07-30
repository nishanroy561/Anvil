"""Anvil Python SDK — trace instrumentation for LLM apps.

Instrument an app in under ten lines:

    import anvil_sdk as anvil

    anvil.init(api_key="ak_...", base_url="http://localhost:8000")

    with anvil.trace("answer_question", input=question) as t:
        with anvil.span("retrieve", type="retrieval") as s:
            s.output = search(question)
        with anvil.span("generate", type="llm") as s:
            s.output = llm(question)
        t.output = s.output

**Prime directive (ADR-0013): this SDK never blocks or crashes the host app.**
Traces are queued in memory and shipped by a background thread; if Anvil is
slow, down, or misconfigured, the queue sheds its oldest entries and counts
them (`anvil.stats()`). Nothing in this package raises into caller code except
exceptions the caller's own function raised.

Spec: plan/sdk.md. Wire format: packages/shared/trace-schema.json.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ._client import AnvilClient
from ._tracer import (
    current_span,
    current_trace,
    observe,
    span,
    start_span,
    start_trace,
    trace,
)
from ._wire import MAX_FIELD_BYTES, Span, Trace

__version__ = "0.1.0"
__all__ = [
    "init",
    "trace",
    "span",
    "observe",
    "start_trace",
    "start_span",
    "current_trace",
    "current_span",
    "feedback",
    "flush",
    "shutdown",
    "stats",
    "get_client",
    "set_client",
    "AnvilClient",
    "Trace",
    "Span",
    "MAX_FIELD_BYTES",
]

log = logging.getLogger("anvil_sdk")

_client: Optional[AnvilClient] = None


def init(
    api_key: str,
    base_url: str = "http://localhost:8000",
    flush_interval: float = 2.0,
    max_queue: int = 1000,
    redact: Optional[Callable[[Any], Any]] = None,
    timeout: float = 5.0,
    **kwargs: Any,
) -> Optional[AnvilClient]:
    """Configure the global client.

    `redact` runs over every trace and span before anything leaves the process —
    scrub PII there. Return the object (mutated is fine) to keep it, or None to
    drop it entirely. A redact hook that raises causes the object to be dropped,
    never shipped un-scrubbed (checklist row 15).
    """
    global _client
    try:
        if _client is not None:
            _client.shutdown()
        _client = AnvilClient(
            api_key=api_key,
            base_url=base_url,
            flush_interval=flush_interval,
            max_queue=max_queue,
            redact=redact,
            timeout=timeout,
            **kwargs,
        )
        return _client
    except Exception:  # noqa: BLE001 - a broken init must not break the app
        log.warning("anvil: init failed; tracing disabled", exc_info=True)
        _client = None
        return None


def get_client() -> Optional[AnvilClient]:
    return _client


def set_client(client: Optional[AnvilClient]) -> None:
    """Point the module-level API at a specific client. Mainly for tests."""
    global _client
    _client = client


def feedback(
    trace_id: str,
    rating: int,
    comment: Optional[str] = None,
    user_ref: Optional[str] = None,
) -> None:
    """Attach an end-user thumbs up/down to a trace (-1 down, +1 up).

    Round-trip `trace.id` to your frontend and send it back with the vote:

        result = chain.invoke(question, config={"callbacks": [handler]})
        return {"answer": result, "trace_id": handler.trace.id}

        # later, when the user clicks 👎
        anvil.feedback(trace_id, -1, comment="wrong date", user_ref=user.id)

    Safe to call before the trace has reached Anvil — the server queues feedback
    and waits for the trace rather than rejecting the race.
    """
    if _client is not None:
        _client.feedback(trace_id, rating, comment=comment, user_ref=user_ref)


def flush() -> None:
    """Best-effort synchronous send of everything queued."""
    if _client is not None:
        _client.flush()


def shutdown() -> None:
    global _client
    if _client is not None:
        _client.shutdown()
        _client = None


def stats() -> dict:
    """Queue/send/drop counters. `dropped` > 0 means Anvil couldn't keep up and
    the SDK shed data to protect your app — raise `max_queue` or check Anvil."""
    return _client.stats.as_dict() if _client is not None else {}
