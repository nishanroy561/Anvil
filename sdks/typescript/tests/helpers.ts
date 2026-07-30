import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
// The 2020 build, not the default draft-07 one — the shared schema declares
// `$schema: draft/2020-12`.
import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import { AnvilClient } from "../src/client.js";
import * as anvil from "../src/index.js";

// The SDK is only correct if what it emits validates against the same schema
// the ingest endpoint enforces (`additionalProperties: false`) — so tests
// validate against the shared source of truth, not a copy.
const schemaPath = fileURLToPath(
  new URL("../../../packages/shared/trace-schema.json", import.meta.url),
);
const schema = JSON.parse(readFileSync(schemaPath, "utf8"));

const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);
const validator = ajv.compile(schema);

export function validateBatch(traces: unknown[]): void {
  if (!validator({ traces })) {
    const errors = (validator.errors ?? [])
      .map((e) => `${e.instancePath} ${e.message}`)
      .join("\n");
    throw new Error(`batch does not match the wire schema:\n${errors}`);
  }
}

export interface FakeServer {
  client: AnvilClient;
  calls: { url: string; body: any; headers: Record<string, string> }[];
  /** Queue the responses each successive flush should get. */
  respond: (...responses: (Response | Error)[]) => void;
  /**
   * Script one endpoint independently. Phase 9 put two endpoints behind one
   * flusher, and their failure modes have to be separable — an ingest 429 must
   * not be able to stall feedback.
   */
  respondTo: (urlFragment: string, ...responses: (Response | Error)[]) => void;
  /** Calls whose URL contains `urlFragment`. */
  callsTo: (urlFragment: string) => FakeServer["calls"];
}

/**
 * A client with no background timer and a scripted fetch: tests drive
 * `flush()` themselves so nothing depends on wall-clock timing.
 */
export function fakeServer(options: Partial<anvil.ClientOptions> = {}): FakeServer {
  const calls: FakeServer["calls"] = [];
  let scripted: (Response | Error)[] = [];
  const perUrl = new Map<string, (Response | Error)[]>();

  const take = (queue: (Response | Error)[]): Response | Error =>
    queue.length > 1 ? queue.shift()! : (queue[0] ?? new Response(null, { status: 202 }));

  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init.body)),
      headers: init.headers as Record<string, string>,
    });
    let queue = scripted;
    for (const [fragment, responses] of perUrl) {
      if (String(url).includes(fragment)) {
        queue = responses;
        break;
      }
    }
    const next = take(queue);
    if (next instanceof Error) throw next;
    return next.clone();
  }) as unknown as typeof fetch;

  const client = new AnvilClient({
    apiKey: "ak_test",
    baseUrl: "https://anvil.test",
    startWorker: false,
    fetchImpl,
    ...options,
  });
  anvil.setClient(client);

  return {
    client,
    calls,
    respond: (...responses) => {
      scripted = responses;
    },
    respondTo: (urlFragment, ...responses) => {
      perUrl.set(urlFragment, responses);
    },
    callsTo: (urlFragment) => calls.filter((c) => c.url.includes(urlFragment)),
  };
}

export function sentTraces(server: FakeServer): any[] {
  return server.callsTo("/v1/ingest/traces").flatMap((c) => c.body.traces);
}

export function sentFeedback(server: FakeServer): any[] {
  return server.callsTo("/v1/feedback").map((c) => c.body);
}
