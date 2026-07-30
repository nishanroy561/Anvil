/**
 * `client.feedback()` — Phase 9's SDK surface (plan/evaluation.md §7).
 *
 * Mirrors `sdks/python/tests/test_feedback.py`. ADR-0013 applies exactly as it
 * does to traces: a thumbs-down is collected on a user's request path, so nothing
 * here may throw. What differs from traces is volume — a handful of votes against
 * thousands of traces — which is why feedback carries its own queue, backoff and
 * counters.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import * as anvil from "../src/index.js";
import { fakeServer, sentFeedback } from "./helpers.js";

const FEEDBACK = "/v1/feedback";
const INGEST = "/v1/ingest/traces";
const TRACE_ID = "3f1c2a5e-0000-4000-8000-000000000001";

afterEach(() => {
  anvil.setClient(null);
  vi.restoreAllMocks();
});

describe("feedback", () => {
  it("posts the vote with the key", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    server.client.feedback({
      traceId: TRACE_ID,
      rating: -1,
      comment: "wrong date",
      userRef: "user-7",
    });
    await server.client.flush();

    expect(sentFeedback(server)).toEqual([
      { trace_id: TRACE_ID, rating: -1, comment: "wrong date", user_ref: "user-7" },
    ]);
    expect(server.callsTo(FEEDBACK)[0].headers.authorization).toBe("Bearer ak_test");
    expect(server.client.stats.feedbackSent).toBe(1);
    expect(server.client.stats.feedbackQueued).toBe(0);
  });

  it("omits optional fields rather than sending nulls", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    server.client.feedback({ traceId: TRACE_ID, rating: 1 });
    await server.client.flush();
    expect(sentFeedback(server)).toEqual([{ trace_id: TRACE_ID, rating: 1 }]);
  });

  it("is reachable from the module-level API", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    anvil.feedback({ traceId: TRACE_ID, rating: -1 });
    await server.client.flush();
    expect(server.callsTo(FEEDBACK)).toHaveLength(1);
  });

  it("is a no-op when no client is configured", () => {
    anvil.setClient(null);
    // A host that never called init() must not crash on a thumbs-down.
    expect(() => anvil.feedback({ traceId: TRACE_ID, rating: -1 })).not.toThrow();
  });

  it("drops an invalid rating instead of throwing", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    vi.spyOn(console, "warn").mockImplementation(() => undefined);

    // A `0` would file feedback that no filter surfaces and no inbox shows.
    for (const rating of [0, 2, -3] as unknown as (-1 | 1)[]) {
      server.client.feedback({ traceId: TRACE_ID, rating });
    }
    await server.client.flush();

    expect(server.callsTo(FEEDBACK)).toHaveLength(0);
    expect(server.client.stats.feedbackDropped).toBe(3);
  });

  it("never throws and keeps the vote when Anvil is unreachable", async () => {
    const server = fakeServer();
    server.respond(new Error("ECONNREFUSED"));
    server.client.feedback({ traceId: TRACE_ID, rating: -1, comment: "keep me" });
    await server.client.flush(1);

    expect(server.client.stats.feedbackSent).toBe(0);
    expect(server.client.stats.feedbackDropped).toBe(0);
    expect(server.client.pendingFeedback).toHaveLength(1);
    expect(server.client.pendingFeedback[0].comment).toBe("keep me");
  });

  it("retries a 5xx then succeeds", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 503 }), new Response(null, { status: 202 }));
    server.client.feedback({ traceId: TRACE_ID, rating: -1 });

    await server.client.flushFeedbackOnce();
    expect(server.client.stats.feedbackSent).toBe(0);
    expect(server.client.feedbackBackoff).toBeGreaterThan(0);
    expect(server.client.pendingFeedback).toHaveLength(1);

    await server.client.flush(1);
    expect(server.client.stats.feedbackSent).toBe(1);
    expect(server.client.feedbackBackoff).toBe(0);
  });

  it("honours Retry-After on 429", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 429, headers: { "retry-after": "5" } }));
    server.client.feedback({ traceId: TRACE_ID, rating: -1 });
    await server.client.flushFeedbackOnce();

    expect(server.client.feedbackBackoff).toBe(5000);
    expect(server.client.pendingFeedback).toHaveLength(1);
  });

  it("drops an unretryable rejection instead of looping on it", async () => {
    const server = fakeServer();
    server.respond(new Response("bad rating", { status: 422 }));
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    server.client.feedback({ traceId: TRACE_ID, rating: -1 });
    await server.client.flush();

    expect(server.callsTo(FEEDBACK)).toHaveLength(1);
    expect(server.client.stats.feedbackDropped).toBe(1);
    expect(server.client.pendingFeedback).toHaveLength(0);
  });

  it("keeps its backoff independent of ingest", async () => {
    // A per-key *ingest* 429 says nothing about the feedback endpoint's own
    // limit; one shared backoff would stall a user's thumbs-down for it.
    const server = fakeServer();
    server.respondTo(INGEST, new Response(null, { status: 429 }));
    server.respondTo(FEEDBACK, new Response(null, { status: 202 }));

    await anvil.trace("t", async () => undefined);
    server.client.feedback({ traceId: TRACE_ID, rating: -1 });

    await server.client.flushOnce();
    expect(server.client.backoff).toBeGreaterThan(0);
    await server.client.flushFeedbackOnce();

    expect(server.callsTo(FEEDBACK)).toHaveLength(1);
    expect(server.client.stats.feedbackSent).toBe(1);
  });

  it("bounds the queue and counts what it sheds", async () => {
    const server = fakeServer({ maxQueue: 3 });
    server.respond(new Response(null, { status: 202 }));
    for (let i = 0; i < 6; i += 1) {
      server.client.feedback({ traceId: TRACE_ID, rating: -1, comment: `c${i}` });
    }
    expect(server.client.pendingFeedback).toHaveLength(3);
    expect(server.client.stats.feedbackDropped).toBe(3);

    await server.client.flush();
    expect(sentFeedback(server).map((b) => b.comment)).toEqual(["c3", "c4", "c5"]);
  });

  it("drains at most its cap per cycle", async () => {
    // The endpoint takes one submission per request, so an unbounded drain would
    // let a feedback backlog monopolise the timer that traces also depend on.
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    for (let i = 0; i < 25; i += 1) {
      server.client.feedback({ traceId: TRACE_ID, rating: -1 });
    }

    await server.client.flushFeedbackOnce();
    expect(server.callsTo(FEEDBACK)).toHaveLength(20);
    expect(server.client.stats.feedbackQueued).toBe(5);
  });

  it("flushes traces and feedback together", async () => {
    // flush() is what a demo script and a shutdown hook both call; a version
    // that drained only traces would silently lose the vote.
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    await anvil.trace("t", async () => undefined);
    server.client.feedback({ traceId: TRACE_ID, rating: -1 });
    await anvil.flush();

    expect(server.callsTo(INGEST)).toHaveLength(1);
    expect(server.callsTo(FEEDBACK)).toHaveLength(1);
  });

  it("ships a queued vote on shutdown, and returns with a dead API", async () => {
    const ok = fakeServer();
    ok.respond(new Response(null, { status: 202 }));
    ok.client.feedback({ traceId: TRACE_ID, rating: 1 });
    await ok.client.shutdown();
    expect(ok.client.stats.feedbackSent).toBe(1);

    const dead = fakeServer();
    dead.respond(new Error("gone"));
    dead.client.feedback({ traceId: TRACE_ID, rating: -1 });
    await dead.client.shutdown(); // must resolve, not hang or throw
    expect(dead.client.stats.feedbackSent).toBe(0);
  });

  it("reports feedback counters separately from trace counters", () => {
    const server = fakeServer();
    const stats = anvil.stats();
    for (const key of ["feedbackQueued", "feedbackSent", "feedbackDropped"]) {
      expect(stats).toHaveProperty(key);
    }
    // The trace counters are untouched, so a dashboard reading `dropped` does
    // not start seeing feedback in it.
    expect(server.client.stats.dropped).toBe(0);
  });
});
