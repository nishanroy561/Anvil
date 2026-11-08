"use client";

import { useEffect, useState } from "react";
import { listApiKeys } from "@/lib/api";
import { CopyButton } from "./ui";

/**
 * The empty state is onboarding: a copy-pasteable SDK snippet carrying the
 * project's real key prefix (plan/dashboard.md ground rules).
 */
export function EmptyTraces({ projectId }: { projectId: string }) {
  const [prefix, setPrefix] = useState<string>("ak_yourkey");
  const [baseUrl, setBaseUrl] = useState<string>("");

  useEffect(() => {
    listApiKeys(projectId)
      .then((keys) => {
        const active = keys.find((k) => k.revoked_at === null);
        if (active) setPrefix(`${active.key_prefix}…`);
      })
      .catch(() => {});
  }, [projectId]);

  useEffect(() => {
    // `init()` defaults `base_url` to http://localhost:8000. Omitting it here
    // was silently fatal on any deployment that is not the reader's own laptop:
    // the SDK never raises (ADR-0013), so a hosted-demo visitor who copied this
    // sent their traces into their own machine, saw no error anywhere, and
    // watched an empty dashboard forever.
    //
    // Derived from the page rather than configured, so it is right on Vercel, on
    // a self-hoster's box and in local dev with no extra build variable to get
    // wrong. `/api` is the same-origin proxy the dashboard itself uses
    // (next.config.mjs), so it reaches the API wherever that happens to live.
    setBaseUrl(`${window.location.origin}/api`);
  }, []);

  // This is the first code a new account ever runs, so it has to be the real
  // API. It previously advertised `from anvil_sdk import Anvil, observe` and a
  // client argument to the decorator — neither exists, so copying it raised
  // ImportError on the first line. Matches the README and `anvil_sdk.__all__`.
  const snippet = `import anvil_sdk as anvil

anvil.init(
    api_key="${prefix}",  # full key from Settings
    base_url="${baseUrl || "…"}",
)

@anvil.observe(type="llm")
def answer(question: str) -> str:
    ...`;

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-lg">
        <h2 className="font-display text-base font-semibold">No traces yet</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">
          Instrument your app with the SDK and traces will stream in here. This
          is the whole setup:
        </p>
        <div className="mt-4 overflow-hidden rounded-lg border border-line bg-iron-900">
          <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
            <span className="font-mono text-2xs text-ink-faint">python</span>
            <CopyButton value={snippet} label="Copy setup snippet" />
          </div>
          <pre className="overflow-x-auto p-3 font-mono text-2xs leading-relaxed text-ink">
            {snippet}
          </pre>
        </div>
        {/*
          anvil-sdk is on PyPI as of 0.1.0 (2026-11-08). The TypeScript SDK is
          not published, and `@anvil` on npm belongs to someone else, so naming
          a package here would send people to an install that fails — say so
          instead, and name the real package once it exists.
        */}
        <p className="mt-3 text-2xs leading-relaxed text-ink-faint">
          <code className="font-mono">pip install anvil-sdk</code> · traces
          appear within seconds of your first request
          <br />
          TypeScript SDK: not yet published to npm
        </p>
      </div>
    </div>
  );
}
