"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";

export interface Filters {
  q: string;
  status: string;
  name: string;
  model: string;
  range: string; // "1h" | "24h" | "7d" | "30d"
  metadataKey: string;
  metadataValue: string;
}

export const DEFAULT_FILTERS: Filters = {
  q: "",
  status: "",
  name: "",
  model: "",
  range: "24h",
  metadataKey: "",
  metadataValue: "",
};

export function rangeToSince(range: string): string | undefined {
  const hours: Record<string, number> = { "1h": 1, "24h": 24, "7d": 168, "30d": 720 };
  const h = hours[range];
  if (!h) return undefined;
  return new Date(Date.now() - h * 3_600_000).toISOString();
}

const RANGES = ["1h", "24h", "7d", "30d"];
const STATUSES = ["", "ok", "error", "in_progress", "abandoned"];

/**
 * Controlled filter bar. Text inputs debounce 400 ms so typing doesn't spray
 * requests; selects apply immediately.
 */
export function FilterBar({
  value,
  onChange,
  autoRefresh,
  onAutoRefresh,
}: {
  value: Filters;
  onChange: (f: Filters) => void;
  autoRefresh: boolean;
  onAutoRefresh: (on: boolean) => void;
}) {
  const [draft, setDraft] = useState(value);

  useEffect(() => setDraft(value), [value]);

  // Debounced commit for the typed fields.
  useEffect(() => {
    const t = setTimeout(() => {
      if (JSON.stringify(draft) !== JSON.stringify(value)) onChange(draft);
    }, 400);
    return () => clearTimeout(t);
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Partial<Filters>) => setDraft((d) => ({ ...d, ...patch }));
  const commit = (patch: Partial<Filters>) => onChange({ ...draft, ...patch });

  const searchTooShort = draft.q.trim().length > 0 && draft.q.trim().length < 3;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* free text */}
      <div className="relative">
        <svg
          viewBox="0 0 16 16"
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint"
          fill="none"
          aria-hidden="true"
        >
          <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4" />
          <path d="m10.5 10.5 3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <input
          value={draft.q}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="Search input/output…"
          aria-label="Free-text search"
          className={clsx("field w-52 pl-8", searchTooShort && "border-status-error/60")}
          title={searchTooShort ? "Search needs at least 3 characters" : undefined}
        />
      </div>

      {/* time range */}
      <div className="flex overflow-hidden rounded-md border border-line" role="group" aria-label="Time range">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => commit({ range: r })}
            aria-pressed={draft.range === r}
            className={clsx(
              "cursor-pointer px-2.5 py-1.5 font-mono text-2xs transition-colors duration-150",
              draft.range === r
                ? "bg-iron-800 text-ink"
                : "bg-iron-900 text-ink-muted hover:text-ink",
            )}
          >
            {r}
          </button>
        ))}
      </div>

      {/* status */}
      <select
        value={draft.status}
        onChange={(e) => commit({ status: e.target.value })}
        aria-label="Status filter"
        className="field cursor-pointer pr-7"
      >
        {STATUSES.map((s) => (
          <option key={s} value={s}>
            {s === "" ? "any status" : s.replace("_", " ")}
          </option>
        ))}
      </select>

      <input
        value={draft.name}
        onChange={(e) => set({ name: e.target.value })}
        placeholder="name"
        aria-label="Trace name filter"
        className="field w-28"
      />
      <input
        value={draft.model}
        onChange={(e) => set({ model: e.target.value })}
        placeholder="model"
        aria-label="Model filter"
        className="field w-36"
      />

      {/* metadata k=v */}
      <div className="flex items-center overflow-hidden rounded-md border border-line bg-iron-900 transition-colors duration-150 focus-within:border-ember">
        <input
          value={draft.metadataKey}
          onChange={(e) => set({ metadataKey: e.target.value })}
          placeholder="meta key"
          aria-label="Metadata key"
          className="h-8 w-24 bg-transparent px-2.5 text-[13px] placeholder:text-ink-faint focus:outline-none"
        />
        <span className="font-mono text-2xs text-ink-faint">=</span>
        <input
          value={draft.metadataValue}
          onChange={(e) => set({ metadataValue: e.target.value })}
          placeholder="value"
          aria-label="Metadata value"
          className="h-8 w-24 bg-transparent px-2.5 text-[13px] placeholder:text-ink-faint focus:outline-none"
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        {/* auto refresh */}
        <button
          type="button"
          onClick={() => onAutoRefresh(!autoRefresh)}
          aria-pressed={autoRefresh}
          className={clsx(
            "flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 font-mono text-2xs transition-colors duration-150",
            autoRefresh
              ? "border-ember/40 bg-ember/10 text-ember"
              : "border-line text-ink-muted hover:text-ink",
          )}
          title="Refresh the list every 10 seconds"
        >
          <span
            className={clsx(
              "h-1.5 w-1.5 rounded-full",
              autoRefresh ? "bg-ember animate-pulse-soft" : "bg-ink-faint",
            )}
            aria-hidden="true"
          />
          live 10s
        </button>

        <button
          type="button"
          onClick={() => onChange({ ...DEFAULT_FILTERS, range: draft.range })}
          className="cursor-pointer text-2xs text-ink-faint transition-colors duration-150 hover:text-ink"
        >
          clear
        </button>
      </div>
    </div>
  );
}
