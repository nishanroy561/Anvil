"use client";

import { useEffect, useState } from "react";
import { getUsage } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { UsageSummary } from "@/lib/types";
import { ErrorNote, Spinner } from "@/components/ui";

/**
 * Settings → Usage (plan/retention.md §2).
 *
 * What is *absent* here is the design: no quota bar, no percentage-of-limit, no
 * "upgrade for more". Anvil has no tiers ([ADR-0019]), so a bar would imply a
 * ceiling and a ceiling implies an upgrade that does not exist. These are two
 * plain counts an operator can act on — how much the app is producing, and how
 * much judging is costing them.
 *
 * A table rather than a chart. Twelve rows of two numbers in a 672px settings
 * column is a reading task, not a seeing one; the bar behind each traces cell
 * carries the trend without needing an axis, a legend or a chart library.
 */

/** Shared by the header and the rows so the columns cannot drift apart. */
const ROW =
  "grid grid-cols-[minmax(64px,auto)_1fr_76px_76px] items-center gap-x-3";

function Headline({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex-1 rounded-lg border border-line bg-iron-900 p-4">
      <p className="font-mono text-2xs uppercase tracking-wider text-ink-faint">{label}</p>
      <p className="mt-1.5 font-mono text-2xl font-medium tabular-nums tracking-tight text-ink">
        {value}
      </p>
      {hint && <p className="mt-0.5 font-mono text-2xs text-ink-muted">{hint}</p>}
    </div>
  );
}

function monthLabel(iso: string): string {
  // The API sends a date; parsing it as UTC keeps a month from sliding backwards
  // for anyone west of Greenwich.
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Plain language, because "30" alone doesn't say what happens to anything. */
function retentionSentence(days: number | null): string {
  if (days === null) {
    return "Traces are kept forever — no retention window is configured.";
  }
  return `Traces are deleted ${days} day${days === 1 ? "" : "s"} after they arrive.`;
}

export function UsagePanel() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    getUsage()
      .then(setUsage)
      .catch(() => setError("Couldn't load usage. Check that the API is running."));
  };

  useEffect(load, []);

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (!usage) {
    return (
      <div className="flex justify-center rounded-lg border border-line bg-iron-900 p-6">
        <Spinner />
      </div>
    );
  }

  const { months, current_month: current } = usage;
  // Each bar is relative to the busiest month shown, so the column is readable
  // whether the operator sends a hundred traces a month or a hundred million.
  const peak = months.reduce((m, r) => Math.max(m, r.traces_ingested), 0);

  return (
    <>
      <div className="mb-3 flex gap-3">
        <Headline
          label="Traces this month"
          value={current ? formatCount(current.traces_ingested) : "—"}
        />
        <Headline
          label="Eval items graded"
          value={current ? formatCount(current.eval_items) : "—"}
          hint="this month"
        />
      </div>

      <div className="overflow-hidden rounded-lg border border-line bg-iron-900">
        {months.length === 0 ? (
          <div className="p-4">
            <p className="text-[13px] text-ink-muted">
              Nothing recorded yet. Usage is rolled up once a day by the worker, so a
              brand-new install stays empty until its first pass — this isn&apos;t an error.
            </p>
          </div>
        ) : (
          <>
            {/* The bar track gets a grid column to itself and the numbers get
                fixed ones. Sharing a flex row with the number, the bar's
                percentage resolved against whatever space the number left over,
                so any month near the peak saturated to the same width — 64,200
                and 72,300 drew identical bars. A fixed track also means every
                row's bar is measured against the same baseline, which is the
                only way lengths across rows mean anything. */}
            <div className={`${ROW} border-b border-line px-4 py-2 font-mono text-2xs uppercase tracking-wider text-ink-faint`}>
              <span>Month</span>
              <span />
              <span className="text-right">Traces</span>
              <span className="text-right">Eval items</span>
            </div>
            <ul>
              {months
                .slice()
                .reverse() /* newest first: the number people came to read */
                .map((m) => (
                  <li
                    key={m.month}
                    className={`${ROW} border-b border-line/50 px-4 py-2 last:border-b-0`}
                  >
                    <span className="font-mono text-2xs text-ink-muted">
                      {monthLabel(m.month)}
                    </span>
                    {/* Decoration for the number beside it, never a substitute:
                        no axis, no absolute value, so it is hidden from the
                        accessibility tree. */}
                    <span className="h-1.5 w-full overflow-hidden rounded-sm bg-line" aria-hidden="true">
                      <span
                        className="block h-full rounded-sm bg-ember/50"
                        style={{
                          width: peak > 0 ? `${Math.max(1.5, (m.traces_ingested / peak) * 100)}%` : "0%",
                        }}
                      />
                    </span>
                    <span className="text-right font-mono text-2xs tabular-nums text-ink">
                      {m.traces_ingested.toLocaleString()}
                    </span>
                    <span className="text-right font-mono text-2xs tabular-nums text-ink-muted">
                      {m.eval_items.toLocaleString()}
                    </span>
                  </li>
                ))}
            </ul>
          </>
        )}
      </div>

      <div className="mt-2 space-y-1">
        <p className="text-2xs leading-relaxed text-ink-muted">
          {retentionSentence(usage.retention_days)}{" "}
          {usage.retention_days !== null && (
            <>
              Datasets are unaffected — a promoted item keeps its own copy of the input and
              outputs, so curated test cases outlive the traffic they came from.
            </>
          )}
        </p>
        {usage.retention_days !== null && (
          <p className="text-2xs leading-relaxed text-ink-faint">
            These counts are what was <em>ingested</em>, so they don&apos;t fall when
            retention deletes the traces later.
          </p>
        )}
      </div>
    </>
  );
}
