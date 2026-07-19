"""Anvil Python SDK — Phase 3, NOT IMPLEMENTED YET. Spec: plan/sdk.md.

Prime directive (ADR-0013): never block or crash the host app. Required
surface: init(), trace()/span() context managers (contextvars nesting),
@observe decorator, background flusher (drop-oldest on full queue),
client-side 100 KB/field truncation, redact hook, AnvilCallbackHandler
for LangChain, client.feedback(). Wire format: packages/shared/trace-schema.json."""

__version__ = "0.0.1"
