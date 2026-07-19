# ADR-0009: Prometheus/Grafana/OTel; the API owns the backlog gauges

Status: Accepted · Date: 2026-07 · Spec: ../observability.md

## Context
An observability platform that can't observe itself is an irony with an on-call cost: the worst failure mode is the ingest worker dying silently while traces pile up in Redis. Naively, the worker exports the queue metrics — but a dead worker exports nothing, blinding exactly the alert that matters.

## Decision
Prometheus + Grafana + OTel (with `traceparent` propagated through Redis stream messages so one distributed trace spans ingest → persist). **Backlog gauges (`anvil_stream_depth`, `anvil_dlq_depth`) are exported by the API process**, which is still alive when the worker is dead; workers export their own processing metrics. Three alert rules cover the distinct failure shapes: consumer-lag growth (stuck worker), target-down + stream-depth (dead worker).

## Consequences
Worker death is visible within one scrape interval. The subtlety (lag freezes when nothing is delivered) is documented in the acceptance test so it doesn't get "simplified" away.

## Alternatives considered
- **Worker exports everything** — the natural layout, and blind in the exact failure it must catch.
- **Separate exporter sidecar for Redis** — works, but another process for two gauges the API can serve.
