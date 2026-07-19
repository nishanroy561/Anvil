# ADR-0012: Entry-point plugin system; cloud loads built-ins only

Status: Accepted · Date: 2026-07 · Spec: ../plugins.md

## Context
Open-core (ADR-0010) makes extensibility strategic: self-hosters who can't add their own graders, alert channels, or model pricing will fork. But a plugin marketplace with sandboxing is far beyond v1 scope, and running arbitrary tenant code in the cloud is a security boundary.

## Decision
Three extension points only — graders, alert channels, model pricing — as Python entry-point groups (`anvil.graders`, `anvil.alert_channels`, `anvil.pricing`) loaded into registries at startup. **Built-ins register through the same contract** (dogfooded). Every plugin call is isolated (try/except + timeout); failures route through each subsystem's existing error path. **Cloud mode loads built-ins only** — no third-party code server-side in v1.

## Consequences
Self-hosters extend by `pip install` into their images; the UI picks plugins up automatically (registry-driven forms). The contract must stay stable — breaking it breaks external packages, so changes need a deprecation note. Cloud customers get plugins only when we curate or sandbox (future ADR).

## Alternatives considered
- **No plugins** — forks become the extension mechanism; worst outcome for an open-core project.
- **Config-file plugins (YAML rules)** — can't express real graders (LLM judges, custom logic).
- **WASM/subprocess sandbox for cloud** — the eventual answer for cloud plugins; wildly out of v1 scope.
- **Broad plugin surface (UI, ingestion transforms)** — every surface is a compatibility promise; three is what we can honor.
