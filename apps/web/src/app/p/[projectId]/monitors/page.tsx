"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  createMonitor,
  deleteMonitor,
  listGraders,
  listMonitors,
  monitorSamples,
  monitorScores,
  updateMonitor,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { GraderInfo, Monitor, MonitorSample, MonitorScoreSeries } from "@/lib/types";
import { ErrorNote, Spinner } from "@/components/ui";

// Same palette as the overview charts, already validated against this surface.
const CHART = {
  score: "#039855",
  grid: "#ECECEA",
  axis: "#72727F",
};

const CRON_PRESETS = [
  { label: "every 10 minutes", value: "*/10 * * * *" },
  { label: "hourly", value: "0 * * * *" },
  { label: "every 6 hours", value: "0 */6 * * *" },
  { label: "daily at 03:00 UTC", value: "0 3 * * *" },
];

/** Mean across this sample's graders, errored ones excluded rather than zeroed. */
function scoreOf(sample: MonitorSample): number | null {
  const values = Object.values(sample.scores ?? {})
    .filter((e) => e && !e.error && e.score !== undefined)
    .map((e) => e.score as number);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function DriftChart({ series }: { series: MonitorScoreSeries }) {
  const points = series.buckets
    .filter((b) => b.mean_score !== null)
    .map((b) => ({
      day: new Date(b.bucket).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      score: b.mean_score as number,
      samples: b.samples,
    }));

  if (points.length < 2) {
    return (
      <p className="px-1 py-6 text-2xs text-ink-muted">
        {points.length === 0
          ? "No scores yet — the chart fills in once the monitor has run."
          : "One day of scores so far. Drift needs at least two points to show."}
      </p>
    );
  }

  return (
    // Width-capped deliberately. Stretched across a full-width card, even a
    // 1.00 -> 0.00 collapse renders as a shallow near-horizontal line and reads
    // as flat — which defeats the one thing a drift chart is for.
    <div className="h-40 max-w-2xl">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis
            dataKey="day"
            stroke={CHART.axis}
            tick={{ fill: CHART.axis, fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={{ stroke: CHART.grid }}
            tickLine={false}
          />
          <YAxis
            // Pinned to 0..1: an auto domain would rescale a 0.02 wobble to fill
            // the panel and make routine noise look like a collapse.
            domain={[0, 1]}
            stroke={CHART.axis}
            tick={{ fill: CHART.axis, fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={{ stroke: CHART.grid }}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "#131316",
              border: "none",
              borderRadius: 8,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
            }}
            labelStyle={{ color: "#9A9AA5" }}
            itemStyle={{ color: "#F6F6F5" }}
            formatter={(value, _name, item) => [
              `${Number(value).toFixed(2)} (${item?.payload?.samples ?? 0} samples)`,
              "mean score",
            ]}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke={CHART.score}
            strokeWidth={2}
            dot={{ r: 2, strokeWidth: 0, fill: CHART.score }}
            activeDot={{ r: 4, strokeWidth: 0 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function MonitorCard({ monitor, onChanged }: { monitor: Monitor; onChanged: () => void }) {
  const [series, setSeries] = useState<MonitorScoreSeries | null>(null);
  const [samples, setSamples] = useState<MonitorSample[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    monitorScores(monitor.id).then(setSeries).catch(() => setSeries(null));
    monitorSamples(monitor.id, 20).then(setSamples).catch(() => setSamples([]));
  }, [monitor.id]);

  // Lowest-scoring first. Sorted by time this is a list of samples; sorted by
  // score it is a list of things to go and look at.
  const worst = (samples ?? [])
    .map((s) => ({ sample: s, score: scoreOf(s) }))
    .filter((s) => s.score !== null)
    .sort((a, b) => (a.score as number) - (b.score as number))
    .slice(0, 5);

  const graders = monitor.grader_config?.graders ?? [];

  return (
    <section className="rounded-lg border border-line bg-iron-900 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-display text-sm font-semibold tracking-tight">{monitor.name}</h2>
        <span className="font-mono text-2xs text-ink-faint">
          {Math.round(monitor.sample_rate * 100)}% of traffic · {monitor.schedule_cron}
        </span>
        <span className="font-mono text-2xs text-ink-faint">
          {monitor.last_run_at ? `last run ${formatDateTime(monitor.last_run_at)}` : "never run"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            disabled={busy}
            aria-pressed={monitor.enabled}
            onClick={async () => {
              setBusy(true);
              try {
                await updateMonitor(monitor.id, { enabled: !monitor.enabled });
                onChanged();
              } finally {
                setBusy(false);
              }
            }}
            className="btn-ghost h-7 px-2 text-2xs"
          >
            {monitor.enabled ? "Disable" : "Enable"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await deleteMonitor(monitor.id);
                onChanged();
              } finally {
                setBusy(false);
              }
            }}
            className="cursor-pointer font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-status-error"
          >
            delete
          </button>
        </div>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-2">
        {!monitor.enabled && (
          <span className="font-mono text-2xs text-status-warn">
            disabled — nothing is being sampled
          </span>
        )}
        {graders.map((g) => (
          <span
            key={g.name}
            className="rounded-full border border-line px-2 py-0.5 font-mono text-2xs text-ink-muted"
          >
            {g.name}
          </span>
        ))}
      </div>

      <div className="mt-3">
        {series ? (
          <DriftChart series={series} />
        ) : (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        )}
      </div>

      {worst.length > 0 && (
        <div className="mt-3 border-t border-line pt-3">
          <h3 className="mb-1.5 font-mono text-2xs uppercase tracking-wider text-ink-faint">
            Lowest-scoring samples
          </h3>
          <ul className="space-y-1">
            {worst.map(({ sample, score }) => (
              <li key={sample.id} className="flex items-center gap-3">
                <span
                  className={`font-mono text-2xs tabular-nums ${
                    (score as number) < 0.5 ? "text-status-error" : "text-ink-muted"
                  }`}
                >
                  {(score as number).toFixed(2)}
                </span>
                <Link
                  href={`/traces/${sample.trace_id}`}
                  className="font-mono text-2xs text-ember hover:underline"
                >
                  {sample.trace_id.slice(0, 8)}
                </Link>
                <span className="font-mono text-2xs text-ink-faint">
                  {formatDateTime(sample.created_at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function CreateMonitorForm({
  projectId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [rate, setRate] = useState(0.1);
  const [cron, setCron] = useState(CRON_PRESETS[0].value);
  const [available, setAvailable] = useState<GraderInfo[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Registry-driven, so an installed plugin appears with no frontend change.
    listGraders().then(setAvailable).catch(() => setAvailable([]));
  }, []);

  // A judge on a monitor is a different commitment from a judge on one eval
  // run: it spends credits on a schedule, unattended, forever.
  const judges = picked.filter((n) => available.find((g) => g.name === n)?.kind === "judge");

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await createMonitor(projectId, {
            name,
            sample_rate: rate,
            schedule_cron: cron,
            graders: picked.map((n) => ({ name: n, config: {} })),
          });
          onCreated();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Couldn't create the monitor.");
        } finally {
          setBusy(false);
        }
      }}
      className="rounded-lg border border-line bg-iron-900 p-4"
    >
      <div className="grid gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">Name</span>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="answer quality"
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Sample rate — {Math.round(rate * 100)}%
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={rate}
            onChange={(e) => setRate(Number(e.target.value))}
            className="mt-2 accent-ember"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Schedule (UTC)
          </span>
          <select
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 text-[13px] text-ink focus:border-ember focus:outline-none"
          >
            {CRON_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="mt-3">
        <legend className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
          Graders
        </legend>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {available.map((g) => (
            <button
              key={g.name}
              type="button"
              aria-pressed={picked.includes(g.name)}
              title={g.description}
              onClick={() =>
                setPicked((p) =>
                  p.includes(g.name) ? p.filter((x) => x !== g.name) : [...p, g.name],
                )
              }
              className={`rounded-full border px-2.5 py-1 font-mono text-2xs transition-colors duration-150 ${
                picked.includes(g.name)
                  ? "border-ember bg-ember/10 text-ember"
                  : "border-line text-ink-muted hover:text-ink"
              }`}
            >
              {g.name}
              {g.kind === "judge" && <span className="ml-1 text-ink-faint">judge</span>}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-2xs leading-relaxed text-ink-muted">
          Live traffic has no reference answer, so graders that compare against an expected
          output (<code className="font-mono">exact_match</code>,{" "}
          <code className="font-mono">accuracy</code>) can&apos;t score here.
        </p>
        {judges.length > 0 && (
          <p className="mt-1 text-2xs leading-relaxed text-status-warn">
            {judges.join(", ")} {judges.length === 1 ? "is a judge" : "are judges"} — this monitor
            will spend provider credits on every sample, on a schedule, unattended. Keep the
            sample rate low.
          </p>
        )}
      </fieldset>

      {error && <p className="mt-2 text-2xs text-status-error">{error}</p>}

      <div className="mt-3 flex items-center gap-2">
        <button type="submit" disabled={busy || picked.length === 0} className="btn-ember">
          {busy ? <Spinner className="text-iron-950" /> : "Create monitor"}
        </button>
        <button type="button" onClick={onCancel} className="btn-ghost">
          Cancel
        </button>
        {picked.length === 0 && (
          <span className="font-mono text-2xs text-ink-faint">pick at least one grader</span>
        )}
      </div>
    </form>
  );
}

export default function MonitorsPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [monitors, setMonitors] = useState<Monitor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setError(null);
    listMonitors(projectId)
      .then(setMonitors)
      .catch(() => setError("Couldn't load monitors. Check that the API is running."));
  }, [projectId]);

  useEffect(load, [load]);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Monitors</h1>
          {monitors && (
            <span className="font-mono text-2xs text-ink-faint">{monitors.length} configured</span>
          )}
        </div>
        <button type="button" onClick={() => setCreating((v) => !v)} className="btn-ember">
          {creating ? "Close" : "New monitor"}
        </button>
      </header>

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {creating && (
          <CreateMonitorForm
            projectId={projectId}
            onCreated={() => {
              setCreating(false);
              load();
            }}
            onCancel={() => setCreating(false)}
          />
        )}

        {monitors === null ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : monitors.length === 0 && !creating ? (
          <div className="flex items-center justify-center py-16">
            <div className="max-w-md text-center">
              <h2 className="font-display text-base font-semibold">No monitors yet</h2>
              <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
                A monitor samples a slice of live traffic on a schedule and scores it, so
                quality drift shows up on a chart instead of in a support ticket. Pair one with
                an alert rule to be told about it.
              </p>
            </div>
          </div>
        ) : (
          monitors.map((m) => <MonitorCard key={m.id} monitor={m} onChanged={load} />)
        )}
      </div>
    </div>
  );
}
