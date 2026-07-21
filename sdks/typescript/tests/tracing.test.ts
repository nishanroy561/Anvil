/** Trace/span shape: nesting, status, observe(), schema conformance. */

import { afterEach, describe, expect, it } from "vitest";

import * as anvil from "../src/index.js";
import { MAX_FIELD_BYTES } from "../src/wire.js";
import { fakeServer, validateBatch, type FakeServer } from "./helpers.js";

let server: FakeServer;

function queued(s: FakeServer): any[] {
  return s.client.pending as any[];
}

afterEach(() => anvil.setClient(null));

describe("tracing", () => {
  it("nests spans by parent and matches the wire schema", async () => {
    server = fakeServer();

    await anvil.trace("answer", { input: { q: "why" } }, async (t) => {
      await anvil.span("retrieve", { type: "retrieval" }, async (outer) => {
        outer.output = ["doc-1"];
        await anvil.span("embed", { type: "embedding" }, (inner) => {
          inner.output = [0.1, 0.2];
        });
      });
      await anvil.span("generate", { type: "llm" }, (gen) => {
        gen.end({ output: "because", tokensIn: 10, tokensOut: 4 });
      });
      t.output = "because";
    });

    const batch = queued(server);
    expect(batch).toHaveLength(1);
    validateBatch(batch);

    const spans = Object.fromEntries(batch[0].spans.map((s: any) => [s.name, s]));
    expect(spans.retrieve.parent_span_id).toBeNull();
    expect(spans.embed.parent_span_id).toBe(spans.retrieve.id);
    expect(spans.generate.parent_span_id).toBeNull();
    expect(spans.generate.tokens_in).toBe(10);
    expect(batch[0].status).toBe("ok");
  });

  it("marks errors and re-throws the host's exception", async () => {
    server = fakeServer();

    await expect(
      anvil.trace("failing", async () => {
        await anvil.span("step", { type: "tool" }, () => {
          throw new Error("boom");
        });
      }),
    ).rejects.toThrow("boom");

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].status).toBe("error");
    expect(batch[0].spans[0].status).toBe("error");
    expect(batch[0].spans[0].error).toContain("boom");
    // The trace still ships despite the failure — errors are the traces you
    // most want to see.
    expect(batch[0].ended_at).not.toBeNull();
  });

  it("keeps parentage correct across concurrent async work", async () => {
    server = fakeServer();

    const work = (label: string) =>
      anvil.span(`outer-${label}`, { type: "chain" }, async () => {
        await new Promise((r) => setTimeout(r, 5));
        await anvil.span(`inner-${label}`, { type: "tool" }, async () => {
          await new Promise((r) => setTimeout(r, 5));
        });
      });

    await anvil.trace("concurrent", async () => {
      await Promise.all([work("a"), work("b")]);
    });

    const batch = queued(server);
    validateBatch(batch);
    const spans = Object.fromEntries(batch[0].spans.map((s: any) => [s.name, s]));
    // AsyncLocalStorage, not a shared stack: interleaved branches must not
    // adopt each other's spans as parents.
    expect(spans["inner-a"].parent_span_id).toBe(spans["outer-a"].id);
    expect(spans["inner-b"].parent_span_id).toBe(spans["outer-b"].id);
    expect(spans["outer-a"].parent_span_id).toBeNull();
    expect(spans["outer-b"].parent_span_id).toBeNull();
  });

  it("observe() captures args and return value", async () => {
    server = fakeServer();

    const lookup = anvil.observe(async (city: string) => `weather:${city}`, {
      name: "lookup",
      type: "tool",
    });

    await anvil.trace("wrapped", async () => {
      expect(await lookup("berlin")).toBe("weather:berlin");
    });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].spans[0].name).toBe("lookup");
    expect(batch[0].spans[0].input).toBe("berlin");
    expect(batch[0].spans[0].output).toBe("weather:berlin");
  });

  it("a span outside any trace still produces a trace", async () => {
    server = fakeServer();
    await anvil.span("standalone", { type: "llm" }, (s) => {
      s.output = "42";
    });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].name).toBe("standalone");
    expect(batch[0].spans).toHaveLength(1);
    expect(batch[0].output).toBe("42");
  });

  it("supports the manual start/end API", async () => {
    server = fakeServer();
    const t = anvil.startTrace("manual", { input: "q" });
    const s = anvil.startSpan(t, "call", { type: "llm" });
    s.end({ output: "a", tokensOut: 3 });
    t.end({ output: "a" });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].id).toBe(t.id);
    expect(batch[0].spans[0].tokens_out).toBe(3);
  });

  it("end() twice is a no-op", async () => {
    server = fakeServer();
    const t = anvil.startTrace("once");
    t.end({ output: "a" });
    t.end({ output: "b" });
    expect(queued(server)).toHaveLength(1);
  });

  it("truncates oversized fields client-side", async () => {
    server = fakeServer();
    const huge = "x".repeat(MAX_FIELD_BYTES + 5000);

    await anvil.trace("big", { input: huge }, async (t) => {
      await anvil.span("step", { type: "llm" }, (s) => {
        s.output = huge;
      });
      t.output = "small";
    });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].input.anvil_truncated).toBe(true);
    expect(batch[0].input.original_bytes).toBeGreaterThan(MAX_FIELD_BYTES);
    expect(batch[0].input.preview.length).toBeLessThanOrEqual(2048);
    expect(batch[0].spans[0].output.anvil_truncated).toBe(true);
    expect(batch[0].output).toBe("small");
  });

  it("survives values JSON cannot handle", async () => {
    server = fakeServer();
    const circular: any = { name: "loop" };
    circular.self = circular;

    await anvil.trace("weird", { input: circular }, async (t) => {
      await anvil.span("step", { type: "custom" }, (s) => {
        s.output = { big: 10n, fn: () => 1 };
      });
      t.output = new Error("kaboom");
    });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].input.self).toBe("[Circular]");
    expect(batch[0].spans[0].output.big).toBe("10");
    expect(batch[0].output).toBe("Error: kaboom");
  });

  it("caps spans at the schema limit", async () => {
    server = fakeServer();
    await anvil.trace("runaway", async () => {
      for (let i = 0; i < 1050; i += 1) {
        await anvil.span(`s${i}`, { type: "custom" }, () => undefined);
      }
    });

    const batch = queued(server);
    validateBatch(batch);
    expect(batch[0].spans).toHaveLength(1000);
  });
});
