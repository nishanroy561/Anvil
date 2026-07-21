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
  calls: { body: any; headers: Record<string, string> }[];
  /** Queue the responses each successive flush should get. */
  respond: (...responses: (Response | Error)[]) => void;
}

/**
 * A client with no background timer and a scripted fetch: tests drive
 * `flush()` themselves so nothing depends on wall-clock timing.
 */
export function fakeServer(options: Partial<anvil.ClientOptions> = {}): FakeServer {
  const calls: FakeServer["calls"] = [];
  let scripted: (Response | Error)[] = [];

  const fetchImpl = (async (_url: string, init: RequestInit) => {
    calls.push({
      body: JSON.parse(String(init.body)),
      headers: init.headers as Record<string, string>,
    });
    const next = scripted.length > 1 ? scripted.shift()! : (scripted[0] ?? new Response(null, { status: 202 }));
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
  };
}

export function sentTraces(server: FakeServer): any[] {
  return server.calls.flatMap((c) => c.body.traces);
}
