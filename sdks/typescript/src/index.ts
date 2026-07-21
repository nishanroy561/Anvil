/**
 * Anvil TypeScript SDK — trace instrumentation for LLM apps.
 *
 * Instrument an app in under ten lines:
 *
 *   import * as anvil from "@anvil/sdk";
 *
 *   anvil.init({ apiKey: "ak_…", baseUrl: "http://localhost:8000" });
 *
 *   await anvil.trace("answer", { input: question }, async (t) => {
 *     const docs = await anvil.span("retrieve", { type: "retrieval" }, () => search(question));
 *     const answer = await anvil.span("generate", { type: "llm" }, () => llm(docs));
 *     t.output = answer;
 *     return answer;
 *   });
 *
 * **Prime directive (ADR-0013): this SDK never blocks or crashes the host app.**
 * Traces are queued in memory and shipped in the background; if Anvil is slow,
 * down, or misconfigured, the queue sheds its oldest entries and counts them
 * (`anvil.stats()`). Nothing here throws into caller code except errors the
 * caller's own function threw.
 *
 * Spec: plan/sdk.md. Wire format: packages/shared/trace-schema.json.
 * Node 18+; browser is out of scope for v1.
 */

import { AnvilClient, type ClientOptions } from "./client.js";

export const VERSION = "0.1.0";

export { AnvilClient } from "./client.js";
export type { ClientOptions, Stats } from "./client.js";
export {
  currentSpan,
  currentTrace,
  observe,
  span,
  startSpan,
  startTrace,
  trace,
} from "./tracer.js";
export { MAX_FIELD_BYTES, Span, Trace } from "./wire.js";
export type { RedactHook, SpanType, Status } from "./wire.js";

let client: AnvilClient | null = null;

/**
 * Configure the global client.
 *
 * `redact` runs over every trace and span before anything leaves the process —
 * scrub PII there. Return the object (mutated is fine) to keep it, or null to
 * drop it. A hook that throws causes the object to be dropped, never shipped
 * un-scrubbed (checklist row 15).
 */
export function init(options: ClientOptions): AnvilClient | null {
  try {
    void client?.shutdown();
    client = new AnvilClient(options);
    return client;
  } catch (error) {
    // A broken init must not break the app.
    console.warn("anvil: init failed; tracing disabled", error);
    client = null;
    return null;
  }
}

export function getClient(): AnvilClient | null {
  return client;
}

/** Point the module-level API at a specific client. Mainly for tests. */
export function setClient(next: AnvilClient | null): void {
  client = next;
}

/** Best-effort synchronous send of everything queued. */
export async function flush(): Promise<void> {
  await client?.flush();
}

export async function shutdown(): Promise<void> {
  await client?.shutdown();
  client = null;
}

/**
 * Queue/send/drop counters. `dropped` > 0 means Anvil could not keep up and the
 * SDK shed data to protect your app — raise `maxQueue` or check Anvil.
 */
export function stats(): Record<string, number> {
  return client ? { ...client.stats } : {};
}
