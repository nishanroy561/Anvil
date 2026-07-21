"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { metricsOverview } from "@/lib/api";
import { formatCost, formatCount, formatDuration } from "@/lib/format";
import type { MetricsOverview } from "@/lib/types";
import { CostChart, TracesChart } from "@/components/OverviewCharts";
import { ErrorNote, Spinner } from "@/components/ui";

const RANGES = [
  { key: "24h", hours: 24, interval: "hour" as const },
  { key: "7d", hours: 168, interval: "day" as const },
  { key: "30d", hours: 720, interval: "day" as const },
];

function StatTile({
  label,
  value,
  sub,
  alarming,
}: {
  label: string;
  value: string;
  sub?: string;
  alarming?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-iron-900 p-4">
      <p className="font-mono text-2xs uppercase tracking-wider text-ink-faint">{label}</p>
      <p
        className={clsx(
          "mt-1.5 font-mono text-2xl font-medium tabular-nums tracking-tight",
          alarming ? "text-status-error" : "text-ink",
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 font-mono text-2xs text-ink-muted">{sub}</p>}
    </div>
  );
}

export default function OverviewPage({ params }: { params: { projectId: string } }) {
  const [range, setRange] = useState(RANGES[0]);
  const [metrics, setMetrics] = useState<MetricsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    setMetrics(null);
    metricsOverview(params.projectId, {
      since: new Date(Date.now() - range.hours * 3_600_000).toISOString(),
      interval: range.interval,
    })
      .then(setMetrics)
      .catch(() => setError("Couldn't load metrics. Check that the API is running."));
  };

  useEffect(load, [params.projectId, range]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <h1 className="font-display text-base font-semibold tracking-tight">Overview</h1>
        <div className="flex overflow-hidden rounded-md border border-line" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              onClick={() => setRange(r)}
              aria-pressed={range.key === r.key}
              className={clsx(
                "cursor-pointer px-2.5 py-1.5 font-mono text-2xs transition-colors duration-150",
                range.key === r.key
                  ? "bg-iron-800 text-ink"
                  : "bg-iron-900 text-ink-muted hover:text-ink",
              )}
            >
              {r.key}
            </button>
          ))}
        </div>
      </header>

      <div className="flex flex-col gap-4 p-4">
        {error && <ErrorNote message={error} onRetry={load} />}

        {!metrics && !error ? (
          <div className="flex h-64 items-center justify-center">
            <Spinner />
          </div>
        ) : metrics ? (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <StatTile label={`Traces · ${range.key}`} value={formatCount(metrics.total_traces)} />
              <StatTile
                label="Error rate"
                value={`${(metrics.error_rate * 100).toFixed(1)}%`}
                alarming={metrics.error_rate > 0.05}
              />
              <StatTile
                label="p95 latency"
                value={formatDuration(metrics.p95_latency_ms)}
                sub={`p50 ${formatDuration(metrics.p50_latency_ms)}`}
              />
              <StatTile
                label="Cost"
                value={formatCost(metrics.total_cost_usd)}
                sub={`${formatCount(metrics.total_tokens_in)} in / ${formatCount(metrics.total_tokens_out)} out tokens`}
              />
            </div>

            {metrics.total_traces === 0 ? (
              <p className="py-8 text-center text-[13px] text-ink-muted">
                No traces in this window. Widen the range, or send some traffic.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <TracesChart metrics={metrics} />
                <CostChart metrics={metrics} />
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
