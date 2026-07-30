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
  /**
   * Feedback keeps its own counters. The two have wildly different volumes —
   * thousands of traces to a handful of thumbs-downs — so one shared `dropped`
   * would let trace loss hide feedback loss completely.
   */
  feedbackQueued: number;
  feedbackSent: number;
  feedbackDropped: number;
}

export interface FeedbackInput {
  traceId: string;
  /** -1 thumbs-down, +1 thumbs-up. */
  rating: -1 | 1;
  comment?: string;
  userRef?: string;
}

interface FeedbackPayload {
  trace_id: string;
  rating: number;
  comment?: string;
  user_ref?: string;
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
  readonly stats: Stats = {
    queued: 0,
    sent: 0,
    dropped: 0,
    failedBatches: 0,
    rejected: 0,
    feedbackQueued: 0,
    feedbackSent: 0,
    feedbackDropped: 0,
  };

  private queue: Record<string, unknown>[] = [];
  /**
   * Feedback rides the same flush timer but keeps its own queue and backoff.
   * Sharing the backoff would let a per-key *ingest* 429 — which says nothing
   * about the feedback endpoint's own limit — stall a user's thumbs-down.
   */
  private feedbackQueue: FeedbackPayload[] = [];
  private feedbackBackoffMs = 0;
  private feedbackNextAttemptAt = 0;
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
      this.timer = setInterval(() => {
        void this.flushOnce();
        void this.flushFeedbackOnce();
      }, this.flushInterval);
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

  /**
   * Attach an end-user thumbs up/down to a trace. Never throws.
   *
   * `traceId` is the id the SDK generated — round-trip it to your frontend
   * (`trace.id`) and send it back with the vote. Pass `userRef` to make repeat
   * votes an upsert, so one user toggling 👎 → 👍 replaces their row rather than
   * adding a contradictory second one.
   *
   * Safe to call before the trace has reached Anvil: the server queues feedback
   * and waits for the trace rather than rejecting the race.
   */
  feedback(input: FeedbackInput): void {
    try {
      if (input.rating !== -1 && input.rating !== 1) {
        // A `0` would file feedback that no filter surfaces and no failure inbox
        // shows, so it is refused rather than stored as "neutral".
        console.warn(`anvil: feedback rating must be -1 or 1, got ${String(input.rating)}`);
        this.stats.feedbackDropped += 1;
        return;
      }
      const payload: FeedbackPayload = { trace_id: input.traceId, rating: input.rating };
      if (input.comment !== undefined) payload.comment = input.comment;
      if (input.userRef !== undefined) payload.user_ref = input.userRef;
      this.feedbackQueue.push(payload);
      while (this.feedbackQueue.length > this.maxQueue) {
        this.feedbackQueue.shift();
        this.stats.feedbackDropped += 1;
      }
      this.stats.feedbackQueued = this.feedbackQueue.length;
    } catch {
      /* ADR-0013: losing feedback beats breaking the caller */
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

  /**
   * Drain up to `maxItems` feedback submissions. The endpoint takes one per
   * request, so the cap is what keeps a feedback backlog from monopolising the
   * flush timer that traces also depend on.
   */
  async flushFeedbackOnce(maxItems = 20): Promise<void> {
    if (Date.now() < this.feedbackNextAttemptAt) return;
    for (let i = 0; i < maxItems; i += 1) {
      const item = this.feedbackQueue.shift();
      if (item === undefined) {
        this.feedbackBackoffMs = 0;
        return;
      }
      this.stats.feedbackQueued = this.feedbackQueue.length;
      if (!(await this.sendFeedback(item))) return;
    }
  }

  /** Returns false if the caller should stop for this cycle. */
  private async sendFeedback(item: FeedbackPayload): Promise<boolean> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/v1/feedback`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          "content-type": "application/json",
          "user-agent": "anvil-sdk-js",
        },
        body: JSON.stringify(item),
        signal: controller.signal,
      });
    } catch {
      this.requeueFeedback(item);
      this.bumpFeedbackBackoff();
      return false;
    } finally {
      clearTimeout(timer);
    }

    if (response.status < 300) {
      this.stats.feedbackSent += 1;
      this.feedbackBackoffMs = 0;
      this.feedbackNextAttemptAt = 0;
      return true;
    }

    if (response.status === 429 || response.status >= 500) {
      this.requeueFeedback(item);
      this.bumpFeedbackBackoff(response.headers?.get?.("retry-after") ?? null);
      return false;
    }

    // 400/401/404/422: the same submission will be refused forever.
    this.stats.feedbackDropped += 1;
    let detail = "";
    try {
      detail = (await response.text()).slice(0, 500);
    } catch {
      /* body already consumed or unreadable */
    }
    console.warn(`anvil: feedback rejected with ${response.status}: ${detail}`);
    return true;
  }

  private requeueFeedback(item: FeedbackPayload): void {
    // Back to the front: it is the oldest submission we hold.
    this.feedbackQueue.unshift(item);
    while (this.feedbackQueue.length > this.maxQueue) {
      this.feedbackQueue.pop();
      this.stats.feedbackDropped += 1;
    }
    this.stats.feedbackQueued = this.feedbackQueue.length;
  }

  private bumpFeedbackBackoff(retryAfter: string | null = null): void {
    const parsed = retryAfter ? Number(retryAfter) : NaN;
    if (Number.isFinite(parsed) && parsed > 0) {
      this.feedbackBackoffMs = Math.min(parsed * 1000, BACKOFF_MAX_MS);
    } else {
      const next = this.feedbackBackoffMs <= 0 ? BACKOFF_BASE_MS : this.feedbackBackoffMs * 2;
      this.feedbackBackoffMs = Math.min(BACKOFF_MAX_MS, next) * (0.5 + Math.random() * 0.5);
    }
    this.feedbackNextAttemptAt = Date.now() + this.feedbackBackoffMs;
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
  get pendingFeedback(): FeedbackPayload[] {
    return this.feedbackQueue;
  }
  get feedbackBackoff(): number {
    return this.feedbackBackoffMs;
  }

  // ---- lifecycle -------------------------------------------------------

  /** Best-effort drain of traces *and* feedback. */
  async flush(maxBatches = 10): Promise<void> {
    this.nextAttemptAt = 0;
    for (let i = 0; i < maxBatches && this.queue.length > 0; i += 1) {
      await this.flushOnce();
      this.nextAttemptAt = 0;
    }
    this.feedbackNextAttemptAt = 0;
    for (let i = 0; i < maxBatches && this.feedbackQueue.length > 0; i += 1) {
      await this.flushFeedbackOnce();
      this.feedbackNextAttemptAt = 0;
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
      this.feedbackBackoffMs = 0;
      await this.flush(3);
    } catch {
      /* nothing useful to do on the way out */
    }
  }
}
