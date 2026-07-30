"""OpenTelemetry export for the worker (plan/observability.md §4).

The worker's half of the API → worker → Postgres trace. The API injects a W3C
`traceparent` into each `ingest:traces` message; `persist_span()` extracts it and
opens a span whose parent is the HTTP request that accepted the batch, so one
ingest is a single trace across two processes and a queue.

Same rules as the API side: **off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set**,
lazily imported, and every failure swallowed. A `nullcontext` is returned when
tracing is off, so callers need no conditional.
"""

import logging
import os
from contextlib import nullcontext

log = logging.getLogger(__name__)

_enabled = False
_tracer = None


def setup() -> bool:
    """Instrument asyncpg and export over OTLP. Safe to call more than once."""
    global _enabled, _tracer

    if _enabled:
        return True

    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the OpenTelemetry packages are "
            "missing — install with: pip install -e '.[otel]'"
        )
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": os.getenv("OTEL_SERVICE_NAME", "anvil-worker"),
                    "service.version": os.getenv("ANVIL_VERSION", "0.1.0"),
                }
            )
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        # The "→ Postgres" leg: instrumenting the driver is what turns the persist
        # span into a tree with the actual INSERTs under it.
        AsyncPGInstrumentor().instrument()

        _tracer = trace.get_tracer("anvil_worker")
        _enabled = True
        log.info("OpenTelemetry tracing on, exporting to %s", endpoint)
        return True
    except Exception:  # noqa: BLE001 - telemetry must never break the worker
        log.exception("OpenTelemetry setup failed; continuing without tracing")
        return False


def persist_span(name: str, traceparents: list[str], batch_size: int):
    """A span for one persist batch, parented to the request that accepted it.

    A batch is drained from the stream and can mix messages from several HTTP
    requests, so there can be several parents. OTel gives a span exactly one, so
    the first is used as the parent and the rest are attached as **links** — which
    is what links are for, and is more honest than silently dropping them or
    inventing a parent that never existed.
    """
    if not _enabled or _tracer is None:
        return nullcontext()

    try:
        from opentelemetry import trace
        from opentelemetry.propagate import extract

        contexts = []
        for parent in traceparents:
            try:
                contexts.append(extract({"traceparent": parent}))
            except Exception:  # noqa: BLE001 - a malformed header costs its link
                continue

        parent_ctx = contexts[0] if contexts else None
        links = []
        for ctx in contexts[1:]:
            span_context = trace.get_current_span(ctx).get_span_context()
            # `extract()` on a garbled header returns an empty context rather than
            # raising, and `get_current_span` then hands back INVALID_SPAN — so
            # without this check a malformed traceparent becomes a link to trace
            # id 0, which Tempo renders as a dangling reference.
            if span_context.is_valid:
                links.append(trace.Link(span_context))

        return _tracer.start_as_current_span(
            name,
            context=parent_ctx,
            links=links or None,
            attributes={
                "anvil.batch.traces": batch_size,
                "anvil.batch.parents": len(contexts),
            },
        )
    except Exception:  # noqa: BLE001
        log.debug("could not open a persist span", exc_info=True)
        return nullcontext()
