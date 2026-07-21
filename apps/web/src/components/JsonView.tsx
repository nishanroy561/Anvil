"use client";

import clsx from "clsx";
import { useState } from "react";
import { isTruncationMarker } from "@/lib/types";

/** Marker ingest leaves when a field blew the size cap — never render it as data. */
export function TruncationNotice({ bytes }: { bytes?: number }) {
  return (
    <div className="rounded-md border border-line bg-iron-850 px-3 py-2 text-2xs text-ink-muted">
      Content truncated at ingest
      {bytes ? ` (original ${(bytes / 1024).toFixed(0)} KB)` : ""} — the SDK caps
      fields at 100 KB.
    </div>
  );
}

function Key({ name }: { name: string }) {
  return <span className="text-span-retrieval">{JSON.stringify(name)}</span>;
}

function Primitive({ value }: { value: unknown }) {
  if (typeof value === "string")
    return <span className="whitespace-pre-wrap break-all text-status-ok">{JSON.stringify(value)}</span>;
  if (typeof value === "number") return <span className="text-span-tool">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="text-span-llm">{String(value)}</span>;
  return <span className="text-ink-faint">null</span>;
}

function Node({ value, depth }: { value: unknown; depth: number }) {
  // Deep levels start collapsed so a large payload opens fast and scannable.
  const [open, setOpen] = useState(depth < 2);

  if (isTruncationMarker(value)) {
    return (
      <span className="text-ink-muted">
        <span className="text-ember">⟨truncated{value.original_bytes ? ` ${(value.original_bytes / 1024).toFixed(0)}KB` : ""}⟩</span>
        {value.preview ? <span className="text-ink-faint"> {JSON.stringify(value.preview.slice(0, 200))}…</span> : null}
      </span>
    );
  }

  if (value === null || typeof value !== "object") return <Primitive value={value} />;

  const isArray = Array.isArray(value);
  const entries = isArray
    ? (value as unknown[]).map((v, i) => [i, v] as const)
    : Object.entries(value as Record<string, unknown>);

  if (entries.length === 0) return <span className="text-ink-faint">{isArray ? "[]" : "{}"}</span>;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="cursor-pointer text-ink-muted hover:text-ink"
        aria-label="Expand"
      >
        {isArray ? `[… ${entries.length}]` : `{… ${entries.length}}`}
      </button>
    );
  }

  return (
    <span>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="cursor-pointer text-ink-muted hover:text-ink"
        aria-label="Collapse"
      >
        {isArray ? "[" : "{"}
      </button>
      <span className="block pl-4">
        {entries.map(([k, v], i) => (
          <span key={String(k)} className="block">
            {!isArray && (
              <>
                <Key name={String(k)} />
                <span className="text-ink-faint">: </span>
              </>
            )}
            <Node value={v} depth={depth + 1} />
            {i < entries.length - 1 && <span className="text-ink-faint">,</span>}
          </span>
        ))}
      </span>
      <span className="text-ink-muted">{isArray ? "]" : "}"}</span>
    </span>
  );
}

/**
 * Pretty JSON with per-node expand/collapse. Strings render as text blocks —
 * most LLM payloads are prose, and quoted-JSON prose is unreadable.
 */
export function JsonView({ value, className }: { value: unknown; className?: string }) {
  if (value === undefined || value === null)
    return <p className="text-2xs text-ink-faint">empty</p>;

  if (isTruncationMarker(value)) {
    return (
      <div className={className}>
        <TruncationNotice bytes={value.original_bytes} />
        {value.preview && (
          <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink-muted">
            {value.preview}
          </pre>
        )}
      </div>
    );
  }

  if (typeof value === "string") {
    return (
      <pre
        className={clsx(
          "whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink",
          className,
        )}
      >
        {value}
      </pre>
    );
  }

  return (
    <div className={clsx("font-mono text-2xs leading-relaxed", className)}>
      <Node value={value} depth={0} />
    </div>
  );
}
