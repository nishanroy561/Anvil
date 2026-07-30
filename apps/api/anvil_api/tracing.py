"""OpenTelemetry export for Anvil's own request path (plan/observability.md §4).

Anvil traces other people's LLM apps; this traces Anvil. The acceptance criterion
is that **one ingest request is followable API → worker → Postgres in Tempo**, and
the interesting part is the middle arrow: the API's only durable act is an XADD to
a Redis stream, and the worker picks that up later, on another connection, in
another process. Nothing carries context across that gap unless it is put there
deliberately — so `services/ingest.py` injects the W3C `traceparent` into each
stream message and the worker extracts it (see `anvil_worker/tracing.py`).

**Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.** Unset is the default and the
common case: instrumentation costs latency on every request, and an install that
is not running the observability profile has nowhere to send spans. It also keeps
the test suite from opening a gRPC connection nobody asked for.

Import is lazy and every failure is swallowed. Self-observability that can stop
the product from serving traffic has its priorities backwards.
"""

import logging
import os

log = logging.getLogger(__name__)

_enabled = False


def enabled() -> bool:
    return _enabled


def setup(app) -> bool:
    """Instrument FastAPI, asyncpg and Redis, and export over OTLP.

    Returns True when tracing is live. Safe to call more than once.
    """
    global _enabled
    if _enabled:
        return True

    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # The extra is not installed. Say so once, clearly, rather than leaving an
        # operator to wonder why Tempo is empty.
        log.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the OpenTelemetry packages are "
            "missing — install with: pip install -e '.[otel]'"
        )
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": os.getenv("OTEL_SERVICE_NAME", "anvil-api"),
                    "service.version": os.getenv("ANVIL_VERSION", "0.1.0"),
                }
            )
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)

        # /healthz and /metrics are polled every few seconds by compose and
        # Prometheus. Tracing them would bury every real request in noise.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")
        # Postgres is the "→ Postgres" in the acceptance criterion; asyncpg is the
        # driver, so instrumenting it is what makes query spans appear.
        AsyncPGInstrumentor().instrument()

        _enabled = True
        log.info("OpenTelemetry tracing on, exporting to %s", endpoint)
        return True
    except Exception:  # noqa: BLE001 - telemetry must never break startup
        log.exception("OpenTelemetry setup failed; continuing without tracing")
        return False


def traceparent() -> str | None:
    """The current span's W3C traceparent, for carrying across the Redis handoff.

    Returns None when tracing is off, which is what makes the ingest path's
    injection a no-op by default rather than a conditional at the call site.
    """
    if not _enabled:
        return None
    try:
        from opentelemetry.propagate import inject

        carrier: dict[str, str] = {}
        inject(carrier)
        return carrier.get("traceparent")
    except Exception:  # noqa: BLE001
        return None
