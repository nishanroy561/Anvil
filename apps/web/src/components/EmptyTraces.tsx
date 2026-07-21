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

  useEffect(() => {
    listApiKeys(projectId)
      .then((keys) => {
        const active = keys.find((k) => k.revoked_at === null);
        if (active) setPrefix(`${active.key_prefix}…`);
      })
      .catch(() => {});
  }, [projectId]);

  const snippet = `from anvil_sdk import Anvil, observe

anvil = Anvil(api_key="${prefix}")  # full key from Settings

@observe(anvil, name="my_pipeline")
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
        <p className="mt-3 text-2xs text-ink-faint">
          pip install anvil-sdk · TypeScript: @anvil/sdk · traces appear within
          seconds of your first request
        </p>
      </div>
    </div>
  );
}
