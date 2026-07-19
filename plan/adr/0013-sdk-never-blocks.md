# ADR-0013: SDKs never block or crash the host app

Status: Accepted · Date: 2026-07 · Spec: ../sdk.md

## Context
The SDK runs inside customers' production request paths. Any Anvil outage, slow endpoint, or SDK bug that blocks or crashes the host app converts a monitoring tool into an incident cause — the fastest way to get uninstalled.

## Decision
Prime directive over delivery guarantees: bounded in-memory queue with background flusher; on backpressure/errors, exponential backoff; on full queue, **drop oldest and count** — never block, never throw into host code; flush on exit best-effort. Client-side truncation and a `redact` hook keep payloads bounded and private.

## Consequences
Under sustained Anvil outage the SDK sheds traces (counted, visible) — observability data is the correct thing to sacrifice; customer requests are not. Acceptance includes killing the Anvil API mid-run and proving the demo app doesn't notice.

## Alternatives considered
- **Guaranteed delivery (local disk spool)** — complexity + disk risk inside customer apps; wrong trade for traces.
- **Synchronous send** — couples customer latency to Anvil's; rejected outright.
