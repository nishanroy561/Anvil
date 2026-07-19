# ADR-0007: Fixed-window Redis rate limiting, fail-open

Status: Accepted · Date: 2026-07 · Spec: ../architecture.md §5

## Context
Four surfaces need limits (auth brute-force, ingest noisy-neighbor, feedback abuse, dashboard polling) with one reusable mechanism. Redis is already on every request path.

## Decision
One `rate_limit(scope, limit, window_s)` dependency: fixed-window `INCR`+`EXPIRE` counters, 429 + `Retry-After`, **fail-open when Redis is down** (a rate limiter must never become the outage). Limits generous and env-configurable.

## Consequences
Trivial to reason about and apply; fixed windows allow ~2× burst at window edges — acceptable at these limits and documented so nobody mistakes it for strict enforcement. Fail-open means Redis loss temporarily removes abuse protection; the alternative (fail-closed auth) is worse.

## Alternatives considered
- **Sliding window / token bucket (Lua)** — smoother enforcement, more code + scripts to maintain; not needed at generous limits.
- **Gateway/nginx limits** — can't scope by API key/org identity, which is the dimension that matters here.
