# ADR-0001: Record architecture decisions as ADRs

Status: Accepted · Date: 2026-07 · Spec: README.md (this folder)

## Context
The plan accumulated 40+ engineering decisions across review rounds, embedded in prose. New contributors (and AI coding agents executing the plan) kept needing the *why* behind choices to avoid relitigating them; a "do not relitigate" paragraph doesn't scale.

## Decision
Every load-bearing decision gets a short ADR in `plan/adr/`, indexed, immutable once accepted, superseded rather than edited. Subsystem docs link to ADRs instead of restating rationale.

## Consequences
Onboarding reads the index to absorb the project's judgment. The cost is discipline: a decision without an ADR is presumed open for debate — so write the ADR when you decide, not later.

## Alternatives considered
- Rationale inline in subsystem docs — is how we started; scattered, unindexed, got relitigated.
- No records — every strong opinion gets re-argued by the next contributor.
