/**
 * Transport behaviour under failure — this file is where ADR-0013 lives.
 * The rule under test throughout: whatever Anvil does, the host app is fine.
 */

import { afterEach, describe, expect, it } from "vitest";

import { BACKOFF_MAX_MS } from "../src/client.js";
import * as anvil from "../src/index.js";
import { fakeServer, sentTraces } from "./helpers.js";

afterEach(() => anvil.setClient(null));

async function emit(n = 1, name = "t"): Promise<void> {
  for (let i = 0; i < n; i += 1) {
    await anvil.trace(`${name}-${i}`, async () => undefined);
  }
}

describe("client", () => {
  it("batches into one request and authenticates", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 202 }));
    await emit(3);
    await server.client.flush();

    expect(server.calls).toHaveLength(1);
    expect(server.calls[0].body.traces).toHaveLength(3);
    expect(server.calls[0].headers.authorization).toBe("Bearer ak_test");
    expect(server.client.stats.sent).toBe(3);
  });

  it("drops the oldest when the queue is full, and counts it", async () => {
    const server = fakeServer({ maxQueue: 5 });
    server.respond(new Response(null, { status: 202 }));
    await emit(8, "trace");

    expect(server.client.pending).toHaveLength(5);
    expect(server.client.stats.dropped).toBe(3);

    await server.client.flush();
    // Oldest dropped, newest kept: recent traces are what you need when
    // debugging the incident that caused the backlog.
    expect(sentTraces(server).map((t) => t.name)).toEqual([
      "trace-3",
      "trace-4",
      "trace-5",
      "trace-6",
      "trace-7",
    ]);
  });

  it("never throws into host code when Anvil is unreachable", async () => {
    const server = fakeServer();
    server.respond(new Error("ECONNREFUSED"));

    await anvil.trace("business-critical", async (t) => {
      await anvil.span("work", { type: "tool" }, (s) => {
        s.output = "done";
      });
      t.output = "done";
    });
    await server.client.flush(1);

    expect(server.client.stats.failedBatches).toBeGreaterThanOrEqual(1);
    expect(server.client.stats.sent).toBe(0);
    expect(server.client.pending).toHaveLength(1); // kept for retry
  });

  it("retries a 5xx then succeeds", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 503 }), new Response(null, { status: 202 }));
    await emit(2);

    await server.client.flushOnce();
    expect(server.client.stats.sent).toBe(0);
    expect(server.client.backoff).toBeGreaterThan(0);
    expect(server.client.pending).toHaveLength(2); // requeued intact

    await server.client.flush(1);
    expect(server.client.stats.sent).toBe(2);
    expect(server.client.backoff).toBe(0);
  });

  it("honours Retry-After on 429", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 429, headers: { "retry-after": "5" } }));
    await emit(1);
    await server.client.flushOnce();

    expect(server.client.backoff).toBe(5000);
    expect(server.client.pending).toHaveLength(1);
  });

  it("backs off exponentially, jittered and capped", async () => {
    const server = fakeServer();
    server.respond(new Response(null, { status: 500 }));
    await emit(1);

    const seen: number[] = [];
    for (let i = 0; i < 12; i += 1) {
      await server.client.flush(1);
      seen.push(server.client.backoff);
    }

    expect(seen[0]).toBeLessThan(seen[3]);
    expect(Math.max(...seen)).toBeLessThanOrEqual(BACKOFF_MAX_MS);
    expect(new Set(seen).size).toBeGreaterThan(1); // jittered, not lockstep
  });

  it("counts in-flight traces as queued", async () => {
    // Found live: polling stats() during an outage intermittently showed 0
    // queued while traces were buffered and retrying, because the count was
    // taken after the batch left the queue and before the failure put it back.
    // A customer diagnosing "is Anvil losing my data?" reads exactly this.
    const observed: number[] = [];
    const server = fakeServer();
    server.respond(new Response(null, { status: 503 }));
    await emit(6);
    expect(server.client.stats.queued).toBe(6);

    const flush = server.client.flushOnce();
    observed.push(server.client.stats.queued);
    await flush;

    expect(observed).toEqual([6]);
    expect(server.client.stats.queued).toBe(6);
  });

  it("drops an unretryable rejection instead of looping on it", async () => {
    const server = fakeServer();
    server.respond(new Response(JSON.stringify({ detail: { code: "invalid_payload" } }), { status: 422 }));
    await emit(2);

    await server.client.flushOnce();
    expect(server.client.stats.rejected).toBe(2);
    expect(server.client.pending).toHaveLength(0);

    await server.client.flushOnce();
    expect(server.calls).toHaveLength(1); // not resent
  });

  it("keeps the queue bounded when a failed batch comes back", async () => {
    const server = fakeServer({ maxQueue: 4 });
    server.respond(new Response(null, { status: 503 }));
    await emit(4);
    await server.client.flushOnce();
    expect(server.client.pending.length).toBeLessThanOrEqual(4);

    await emit(4);
    expect(server.client.pending.length).toBeLessThanOrEqual(4);
    expect(server.client.stats.dropped).toBeGreaterThan(0);
  });

  it("shutdown flushes, and returns even with a dead API", async () => {
    const ok = fakeServer();
    ok.respond(new Response(null, { status: 202 }));
    await emit(2);
    await ok.client.shutdown();
    expect(ok.client.stats.sent).toBe(2);

    const dead = fakeServer();
    dead.respond(new Error("gone"));
    await emit(2);
    await dead.client.shutdown(); // must resolve, not hang or throw
    expect(dead.client.stats.sent).toBe(0);
  });

  it("is a no-op when no client is configured", async () => {
    anvil.setClient(null);
    await anvil.trace("orphan", async (t) => {
      await anvil.span("step", { type: "tool" }, () => undefined);
      t.output = "fine";
    });
    expect(anvil.stats()).toEqual({});
  });

  // ---- redact (checklist row 15) ---------------------------------------

  it("scrubs via redact before anything leaves the process", async () => {
    const server = fakeServer({
      redact: (obj) => {
        const input = obj.input as any;
        if (input && typeof input === "object" && "email" in input) {
          obj.input = { ...input, email: "[redacted]" };
        }
        return obj;
      },
    });
    server.respond(new Response(null, { status: 202 }));

    await anvil.trace("signup", { input: { email: "a@b.com", plan: "pro" } }, async (t) => {
      await anvil.span("verify", { type: "tool" }, (s) => {
        s.input = { email: "a@b.com" };
      });
      t.output = "ok";
    });
    await server.client.flush();

    const wire = JSON.stringify(server.calls[0].body);
    expect(wire).not.toContain("a@b.com");
    expect(wire.match(/\[redacted\]/g)).toHaveLength(2);
  });

  it("drops the trace when redact returns null", async () => {
    const server = fakeServer({ redact: () => null });
    await emit(1);
    expect(server.client.pending).toHaveLength(0);
  });

  it("drops rather than leaks when redact throws", async () => {
    const server = fakeServer({
      redact: () => {
        throw new Error("bad regex");
      },
    });
    await emit(1);
    expect(server.client.pending).toHaveLength(0);
  });
});
