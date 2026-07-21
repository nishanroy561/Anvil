/**
 * Trace/span objects and their serialization to the Anvil wire format.
 *
 * The wire format is `packages/shared/trace-schema.json` and the ingest
 * endpoint validates it with `additionalProperties: false` — an extra key here
 * is a 422 for the customer, so `toWire()` emits exactly the schema's fields.
 */

import { randomUUID } from "node:crypto";

export type SpanType = "llm" | "retrieval" | "tool" | "embedding" | "chain" | "custom";
export type Status = "ok" | "error" | "in_progress";

const SPAN_TYPES: SpanType[] = ["llm", "retrieval", "tool", "embedding", "chain", "custom"];

/**
 * Mirrors the server policy (plan/ingestion.md §1). Truncating here keeps
 * batches well under the 5 MB body cap, so an app logging huge prompts degrades
 * to previews instead of 413s (checklist row 6, client half).
 */
export const MAX_FIELD_BYTES = 100_000;
const PREVIEW_CHARS = 2048;
const TRUNCATED_FLAG = "anvil_truncated";

export function newId(): string {
  // Client-generated UUIDs make ingestion idempotent — a retried batch upserts
  // the same rows rather than duplicating them.
  return randomUUID();
}

function iso(value: Date | null | undefined): string | null {
  return value ? value.toISOString() : null;
}

/** JSON-encode without ever throwing into host code (circular refs, BigInt…). */
function encode(value: unknown): string {
  const seen = new WeakSet();
  try {
    return JSON.stringify(value, (_key, val) => {
      if (typeof val === "bigint") return val.toString();
      if (typeof val === "function") return `[Function ${val.name || "anonymous"}]`;
      if (typeof val === "object" && val !== null) {
        if (seen.has(val as object)) return "[Circular]";
        seen.add(val as object);
      }
      return val;
    }) ?? "null";
  } catch {
    return String(value);
  }
}

export function coerce(value: unknown): unknown {
  if (value === undefined) return null;
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return value;
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  try {
    return JSON.parse(encode(value));
  } catch {
    return String(value);
  }
}

export function truncateField(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  const encoded = encode(value);
  const size = Buffer.byteLength(encoded, "utf8");
  if (size <= MAX_FIELD_BYTES) return value;
  return {
    [TRUNCATED_FLAG]: true,
    original_bytes: size,
    preview: encoded.slice(0, PREVIEW_CHARS),
  };
}

export interface SpanInit {
  name: string;
  type?: SpanType;
  input?: unknown;
  attributes?: Record<string, unknown>;
  parentSpanId?: string | null;
}

export interface EndSpanFields {
  output?: unknown;
  status?: Status;
  error?: string | null;
  tokensIn?: number | null;
  tokensOut?: number | null;
  attributes?: Record<string, unknown>;
}

export class Span {
  readonly id = newId();
  name: string;
  type: SpanType;
  parentSpanId: string | null;
  status: Status = "in_progress";
  input: unknown;
  output: unknown = null;
  attributes: Record<string, unknown>;
  tokensIn: number | null = null;
  tokensOut: number | null = null;
  error: string | null = null;
  startedAt = new Date();
  endedAt: Date | null = null;

  /** Set by the manual API so `.end()` can close the async-context scope. */
  onEnd: ((span: Span) => void) | null = null;

  constructor(init: SpanInit) {
    this.name = init.name;
    this.type = init.type && SPAN_TYPES.includes(init.type) ? init.type : "custom";
    this.input = init.input ?? null;
    this.attributes = { ...(init.attributes ?? {}) };
    this.parentSpanId = init.parentSpanId ?? null;
  }

  end(fields: EndSpanFields = {}): this {
    if (fields.output !== undefined) this.output = fields.output;
    if (fields.error != null) this.error = fields.error;
    if (fields.tokensIn != null) this.tokensIn = fields.tokensIn;
    if (fields.tokensOut != null) this.tokensOut = fields.tokensOut;
    if (fields.attributes) Object.assign(this.attributes, fields.attributes);
    this.status = fields.status ?? (this.error ? "error" : "ok");
    if (this.endedAt === null) this.endedAt = new Date();
    if (this.onEnd) {
      const hook = this.onEnd;
      this.onEnd = null; // idempotent: end() twice is a no-op
      hook(this);
    }
    return this;
  }

  toWire(): Record<string, unknown> {
    return {
      id: this.id,
      parent_span_id: this.parentSpanId,
      name: String(this.name).slice(0, 512),
      type: this.type,
      status: this.status,
      input: truncateField(coerce(this.input)),
      output: truncateField(coerce(this.output)),
      attributes: (coerce(this.attributes) as Record<string, unknown>) ?? {},
      tokens_in: this.tokensIn,
      tokens_out: this.tokensOut,
      error: this.error,
      started_at: iso(this.startedAt),
      ended_at: iso(this.endedAt),
    };
  }
}

export interface TraceInit {
  name: string;
  input?: unknown;
  metadata?: Record<string, unknown>;
  model?: string | null;
  sessionId?: string | null;
  userRef?: string | null;
}

export interface EndTraceFields {
  output?: unknown;
  status?: Status;
  metadata?: Record<string, unknown>;
}

export class Trace {
  readonly id = newId();
  name: string;
  status: Status = "in_progress";
  input: unknown;
  output: unknown = null;
  metadata: Record<string, unknown>;
  model: string | null;
  sessionId: string | null;
  userRef: string | null;
  startedAt = new Date();
  endedAt: Date | null = null;
  spans: Span[] = [];

  onEnd: ((trace: Trace) => void) | null = null;

  constructor(init: TraceInit) {
    this.name = init.name;
    this.input = init.input ?? null;
    this.metadata = { ...(init.metadata ?? {}) };
    this.model = init.model ?? null;
    this.sessionId = init.sessionId ?? null;
    this.userRef = init.userRef ?? null;
  }

  end(fields: EndTraceFields = {}): this {
    if (fields.output !== undefined) this.output = fields.output;
    if (fields.metadata) Object.assign(this.metadata, fields.metadata);
    this.status = fields.status ?? "ok";
    if (this.endedAt === null) this.endedAt = new Date();
    if (this.onEnd) {
      const hook = this.onEnd;
      this.onEnd = null;
      hook(this);
    }
    return this;
  }

  toWire(): Record<string, unknown> {
    return {
      id: this.id,
      name: String(this.name).slice(0, 512),
      status: this.status,
      input: truncateField(coerce(this.input)),
      output: truncateField(coerce(this.output)),
      metadata: (coerce(this.metadata) as Record<string, unknown>) ?? {},
      model: this.model,
      session_id: this.sessionId,
      user_ref: this.userRef,
      started_at: iso(this.startedAt),
      ended_at: iso(this.endedAt),
      // Schema caps spans at 1000; drop the overflow rather than have the whole
      // trace rejected for it.
      spans: this.spans.slice(0, 1000).map((s) => s.toWire()),
    };
  }
}

export type RedactHook = (obj: Trace | Span) => Trace | Span | null | undefined;

/**
 * Runs the customer's redact hook over the trace and each span. Returns false
 * if the trace should be dropped. A hook that throws drops the object rather
 * than shipping it un-scrubbed — a buggy scrubber must not leak, and must not
 * crash the host either.
 */
export function applyRedact(trace: Trace, redact: RedactHook | undefined): boolean {
  if (!redact) return true;
  let result: Trace | Span | null | undefined;
  try {
    result = redact(trace);
  } catch {
    return false;
  }
  if (!result) return false;
  const kept: Span[] = [];
  for (const span of (result as Trace).spans) {
    try {
      const scrubbed = redact(span);
      if (scrubbed) kept.push(scrubbed as Span);
    } catch {
      /* drop this span */
    }
  }
  (result as Trace).spans = kept;
  return true;
}
