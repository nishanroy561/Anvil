/**
 * Trace/span scoping and the observe() wrapper.
 *
 * Nesting rides on AsyncLocalStorage, so it survives await boundaries and
 * concurrent requests without the caller threading a context object through
 * every function. Nothing here throws on Anvil's behalf: instrumentation
 * failures are swallowed, but errors from the *host's* own code are always
 * re-thrown after being recorded.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import { Span, Trace, type SpanType, type TraceInit } from "./wire.js";
import { getClient } from "./index.js";

interface Scope {
  trace: Trace | null;
  span: Span | null;
}

const storage = new AsyncLocalStorage<Scope>();

export function currentTrace(): Trace | null {
  return storage.getStore()?.trace ?? null;
}

export function currentSpan(): Span | null {
  return storage.getStore()?.span ?? null;
}

function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`.slice(0, 2000);
  return String(error).slice(0, 2000);
}

function ship(trace: Trace): void {
  try {
    getClient()?.submit(trace);
  } catch {
    /* never break the host */
  }
}

export interface TraceOptions extends Omit<TraceInit, "name"> {}

export interface SpanOptions {
  type?: SpanType;
  input?: unknown;
  attributes?: Record<string, unknown>;
}

/**
 * Root of a unit of work. `t.id` is a client-generated UUID, exposed so callers
 * can round-trip it to their frontend for feedback later.
 *
 *   const answer = await anvil.trace("ask", { input: q }, async (t) => { … });
 */
export async function trace<T>(
  name: string,
  options: TraceOptions,
  fn: (trace: Trace) => T | Promise<T>,
): Promise<T>;
export async function trace<T>(name: string, fn: (trace: Trace) => T | Promise<T>): Promise<T>;
export async function trace<T>(
  name: string,
  optionsOrFn: TraceOptions | ((trace: Trace) => T | Promise<T>),
  maybeFn?: (trace: Trace) => T | Promise<T>,
): Promise<T> {
  const options = typeof optionsOrFn === "function" ? {} : optionsOrFn;
  const fn = (typeof optionsOrFn === "function" ? optionsOrFn : maybeFn)!;
  const obj = new Trace({ name, ...options });

  return storage.run({ trace: obj, span: null }, async () => {
    try {
      const result = await fn(obj);
      if (obj.endedAt === null) obj.end({ status: "ok" });
      return result;
    } catch (error) {
      obj.metadata.error ??= describe(error);
      if (obj.endedAt === null) obj.end({ status: "error" });
      throw error;
    } finally {
      ship(obj);
    }
  });
}

/**
 * A step inside a trace. Nests under whatever span is active, if any. With no
 * active trace the span becomes its own single-span trace rather than being
 * dropped — a wrapped helper called outside a request still produces something
 * you can look at.
 */
export async function span<T>(
  name: string,
  options: SpanOptions,
  fn: (span: Span) => T | Promise<T>,
): Promise<T>;
export async function span<T>(name: string, fn: (span: Span) => T | Promise<T>): Promise<T>;
export async function span<T>(
  name: string,
  optionsOrFn: SpanOptions | ((span: Span) => T | Promise<T>),
  maybeFn?: (span: Span) => T | Promise<T>,
): Promise<T> {
  const options = typeof optionsOrFn === "function" ? {} : optionsOrFn;
  const fn = (typeof optionsOrFn === "function" ? optionsOrFn : maybeFn)!;
  const scope = storage.getStore();

  if (!scope?.trace) {
    return trace(name, { input: options.input }, async (t) => {
      // Recursing now takes the normal path: a trace is in scope.
      const result = await span(name, options, fn);
      t.output = t.spans[0]?.output ?? null;
      return result;
    });
  }

  const obj = new Span({ ...options, name, parentSpanId: scope.span?.id ?? null });
  scope.trace.spans.push(obj);

  return storage.run({ trace: scope.trace, span: obj }, async () => {
    try {
      const result = await fn(obj);
      if (obj.endedAt === null) obj.end({ status: "ok" });
      return result;
    } catch (error) {
      obj.error = describe(error);
      if (obj.endedAt === null) obj.end({ status: "error" });
      throw error;
    }
  });
}

/**
 * Manual API for code that cannot wrap a callback (middleware, event handlers).
 * Call `.end({ output })` to close and submit.
 */
export function startTrace(name: string, options: TraceOptions = {}): Trace {
  const obj = new Trace({ name, ...options });
  obj.onEnd = ship;
  return obj;
}

/** Manual counterpart to `span()`; attaches to an explicitly passed trace. */
export function startSpan(parent: Trace, name: string, options: SpanOptions = {}): Span {
  const obj = new Span({ ...options, name, parentSpanId: null });
  parent.spans.push(obj);
  return obj;
}

/**
 * Wrapper sugar: `const fetchDocs = observe(rawFetchDocs, { type: "retrieval" })`.
 * Arguments become the span input, the resolved value its output, and a
 * rejection marks the span errored before propagating.
 */
export function observe<A extends unknown[], R>(
  fn: (...args: A) => R | Promise<R>,
  options: { name?: string; type?: SpanType; captureInput?: boolean; captureOutput?: boolean } = {},
): (...args: A) => Promise<R> {
  const spanName = options.name ?? fn.name ?? "anonymous";
  return async (...args: A): Promise<R> =>
    span(
      spanName,
      {
        type: options.type ?? "custom",
        input: options.captureInput === false ? null : args.length === 1 ? args[0] : args,
      },
      async (active) => {
        const result = await fn(...args);
        if (options.captureOutput !== false) active.output = result;
        return result;
      },
    );
}
