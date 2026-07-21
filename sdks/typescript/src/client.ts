/**
 * Bounded queue + background flusher (ADR-0013).
 *
 * The prime directive is that nothing in here can block or crash the host app.
 * Concretely: `submit()` never throws, the queue is bounded and sheds its
 * *oldest* entries when full, all transport errors are swallowed after being
 * counted, and the flush timer is `unref()`d so a wedged Anvil can never hold
 * the Node event loop open.
 */

import { Trace, applyRedact, type RedactHook } from "./wire.js";

export const DEFAULT_BASE_URL = "http://localhost:8000";
/** The wire schema caps a batch at 500 traces. */
export const MAX_BATCH = 500;
export const BACKOFF_BASE_MS = 500;
export const BACKOFF_MAX_MS = 30_000;

export interface Stats {
  queued: number;
  sent: number;
  /** Traces shed because the queue was full. Counted, never silent. */
  dropped: number;
  failedBatches: number;
  rejected: number;
}

export interface ClientOptions {
  apiKey: string;
  baseUrl?: string;
  /** Milliseconds between background flushes. */
  flushInterval?: number;
  maxQueue?: number;
  redact?: RedactHook;
  timeout?: number;
  /** Injectable for tests; defaults to global fetch (Node 18+). */
  fetchImpl?: typeof fetch;
  startWorker?: boolean;
}

export class AnvilClient {
  readonly apiKey: string;
  readonly baseUrl: string;
  readonly flushInterval: number;
  readonly maxQueue: number;
  redact?: RedactHook;
  readonly stats: Stats = { queued: 0, sent: 0, dropped: 0, failedBatches: 0, rejected: 0 };

  private queue: Record<string, unknown>[] = [];
  /**
   * Traces handed to an in-flight request. Counted as still queued: during an
   * outage a customer polling stats() must never see "0 queued" while their
   * data is actually buffered and retrying.
   */
  private inflightCount = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private backoffMs = 0;
  private nextAttemptAt = 0;
  private inFlight: Promise<void> | null = null;
  private stopped = false;
  private readonly timeout: number;
  private readonly fetchImpl: typeof fetch;
  private readonly onExit = () => {
    void this.shutdown();
  };

  constructor(options: ClientOptions) {
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.flushInterval = options.flushInterval ?? 2000;
    this.maxQueue = options.maxQueue ?? 1000;
    this.redact = options.redact;
    this.timeout = options.timeout ?? 5000;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;

    if (options.startWorker !== false) {
      this.timer = setInterval(() => void this.flushOnce(), this.flushInterval);
      // Without unref(), a process whose work is done still waits on our timer.
      this.timer.unref?.();
      process.once("beforeExit", this.onExit);
    }
  }

  // ---- producer side ---------------------------------------------------

  /** Queue a finished trace. Never blocks, never throws. */
  submit(trace: Trace): void {
    try {
      if (!applyRedact(trace, this.redact)) return;
      this.queue.push(trace.toWire());
      while (this.queue.length > this.maxQueue) {
        // Drop oldest: recent traces are the ones you need when you are
        // debugging the incident that caused the backlog.
        this.queue.shift();
        this.stats.dropped += 1;
      }
      this.syncQueued();
    } catch {
      /* ADR-0013: losing a trace beats breaking the caller */
    }
  }

  // ---- consumer side ---------------------------------------------------

  async flushOnce(): Promise<void> {
    if (this.inFlight) return this.inFlight;
    if (Date.now() < this.nextAttemptAt) return;
    if (this.queue.length === 0) {
      this.backoffMs = 0;
      return;
    }
    const batch = this.queue.splice(0, MAX_BATCH);
    this.inflightCount = batch.length;
    this.syncQueued();

    this.inFlight = this.send(batch).finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  private async send(batch: Record<string, unknown>[]): Promise<void> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/v1/ingest/traces`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          "content-type": "application/json",
          "user-agent": "anvil-sdk-js",
        },
        body: JSON.stringify({ traces: batch }),
        signal: controller.signal,
      });
    } catch {
      // Network down, DNS gone, timed out.
      this.stats.failedBatches += 1;
      this.requeue(batch);
      this.bumpBackoff();
      return;
    } finally {
      clearTimeout(timer);
    }

    if (response.status < 300) {
      this.stats.sent += batch.length;
      this.inflightCount = 0;
      this.syncQueued();
      this.backoffMs = 0;
      this.nextAttemptAt = 0;
      return;
    }

    if (response.status === 429 || response.status >= 500) {
      // Retryable: rate limited, backpressured, or Anvil is unwell.
      this.stats.failedBatches += 1;
      this.requeue(batch);
      this.bumpBackoff(response.headers?.get?.("retry-after") ?? null);
      return;
    }

    // 400/401/413/422: retrying sends the same bad batch forever. Drop it,
    // loudly enough that a misconfigured key is findable in the host's logs.
    this.stats.rejected += batch.length;
    this.inflightCount = 0;
    this.syncQueued();
    this.backoffMs = 0;
    let detail = "";
    try {
      detail = (await response.text()).slice(0, 500);
    } catch {
      /* body already consumed or unreadable */
    }
    console.warn(`anvil: ingest rejected ${batch.length} traces with ${response.status}: ${detail}`);
  }

  private syncQueued(): void {
    this.stats.queued = this.queue.length + this.inflightCount;
  }

  /**
   * Put a failed batch back at the front (it is the oldest data). If the queue
   * filled while we were away, the newest entries are dropped and counted.
   */
  private requeue(batch: Record<string, unknown>[]): void {
    this.inflightCount = 0;
    this.queue = batch.concat(this.queue);
    while (this.queue.length > this.maxQueue) {
      this.queue.pop();
      this.stats.dropped += 1;
    }
    this.syncQueued();
  }

  private bumpBackoff(retryAfter: string | null = null): void {
    const parsed = retryAfter ? Number(retryAfter) : NaN;
    if (Number.isFinite(parsed) && parsed > 0) {
      this.backoffMs = Math.min(parsed * 1000, BACKOFF_MAX_MS);
    } else {
      const next = this.backoffMs <= 0 ? BACKOFF_BASE_MS : this.backoffMs * 2;
      // Full jitter: without it, every SDK instance that failed during an
      // outage retries in lockstep and re-DDoSes the API on recovery.
      this.backoffMs = Math.min(BACKOFF_MAX_MS, next) * (0.5 + Math.random() * 0.5);
    }
    this.nextAttemptAt = Date.now() + this.backoffMs;
  }

  /** Test/introspection helpers. */
  get pending(): Record<string, unknown>[] {
    return this.queue;
  }
  get backoff(): number {
    return this.backoffMs;
  }

  // ---- lifecycle -------------------------------------------------------

  /** Best-effort drain. */
  async flush(maxBatches = 10): Promise<void> {
    this.nextAttemptAt = 0;
    for (let i = 0; i < maxBatches && this.queue.length > 0; i += 1) {
      await this.flushOnce();
      this.nextAttemptAt = 0;
    }
  }

  /**
   * Flush what we can on the way out. Best-effort by design: a hung Anvil must
   * not add seconds to the host process's exit.
   */
  async shutdown(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    process.removeListener?.("beforeExit", this.onExit);
    try {
      this.backoffMs = 0;
      await this.flush(3);
    } catch {
      /* nothing useful to do on the way out */
    }
  }
}
