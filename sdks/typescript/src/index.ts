/**
 * Anvil TypeScript SDK — Phase 3, NOT IMPLEMENTED YET. Spec: plan/sdk.md.
 * Mirrors the Python SDK: init(), trace()/span() via AsyncLocalStorage,
 * observe() wrapper, background flusher (drop-oldest, never block — ADR-0013),
 * client-side truncation + redact hook, client.feedback().
 * Wire format: packages/shared/trace-schema.json. Node 18+; browser out of scope v1.
 */
export const VERSION = "0.0.1";
