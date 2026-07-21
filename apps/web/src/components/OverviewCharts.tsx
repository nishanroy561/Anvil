"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCost } from "@/lib/format";
import type { MetricsOverview } from "@/lib/types";

/**
 * Chart hues sit in the OKLCH L [0.48, 0.67] band against the white surface so
 * marks stay legible without shouting. Errors always sit at the base of the
 * stack — position, gaps, and the legend carry identity alongside hue
 * (red/green alone is not enough).
 */
const CHART = {
  ok: "#039855",
  error: "#D5544A",
  cost: "#3B76FF",
  grid: "#ECECEA",
  axis: "#72727F",
};

interface BucketRow {
  label: string;
  ok: number;
  errors: number;
  cost: number;
}

function toRows(m: MetricsOverview): BucketRow[] {
  return m.buckets.map((b) => {
    const d = new Date(b.bucket);
    return {
      label:
        m.interval === "hour"
          ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })
          : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      ok: b.traces - b.errors,
      errors: b.errors,
      cost: b.cost_usd,
    };
  });
}

function DarkTooltip({
  active,
  payload,
  label,
  money,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color?: string }[];
  label?: string;
  money?: boolean;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-line bg-iron-850 px-2.5 py-1.5 font-mono text-2xs shadow-panel">
      <p className="mb-1 text-ink-faint">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5 text-ink">
          <span className="h-1.5 w-1.5 rounded-sm" style={{ background: p.color }} />
          {p.name}: {money ? formatCost(p.value) : p.value}
        </p>
      ))}
    </div>
  );
}

function ChartFrame({
  title,
  legend,
  children,
}: {
  title: string;
  legend?: { label: string; color: string }[];
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-line bg-iron-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-2xs uppercase tracking-wider text-ink-muted">{title}</h2>
        {legend && (
          <ul className="flex items-center gap-3">
            {legend.map((l) => (
              <li key={l.label} className="flex items-center gap-1.5 font-mono text-2xs text-ink-muted">
                <span className="h-1.5 w-1.5 rounded-sm" style={{ background: l.color }} aria-hidden="true" />
                {l.label}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="h-44">{children}</div>
    </section>
  );
}

const AXIS_PROPS = {
  stroke: CHART.axis,
  tick: { fill: CHART.axis, fontSize: 10, fontFamily: "var(--font-mono)" },
  tickLine: false,
  axisLine: { stroke: CHART.grid },
};

export function TracesChart({ metrics }: { metrics: MetricsOverview }) {
  const rows = toRows(metrics);
  return (
    <ChartFrame
      title={`Traces / ${metrics.interval}`}
      legend={[
        { label: "ok", color: CHART.ok },
        { label: "error", color: CHART.error },
      ]}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 4, right: 4, left: -18, bottom: 0 }} barCategoryGap="25%">
          <CartesianGrid stroke={CHART.grid} strokeDasharray="0" vertical={false} />
          <XAxis dataKey="label" {...AXIS_PROPS} interval="preserveStartEnd" minTickGap={32} />
          <YAxis {...AXIS_PROPS} allowDecimals={false} />
          <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(19,19,22,0.04)" }} />
          {/* errors at the base: position encodes identity alongside hue */}
          <Bar dataKey="errors" name="error" stackId="t" fill={CHART.error} radius={[0, 0, 0, 0]} />
          <Bar dataKey="ok" name="ok" stackId="t" fill={CHART.ok} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function CostChart({ metrics }: { metrics: MetricsOverview }) {
  const rows = toRows(metrics);
  return (
    <ChartFrame title={`Cost / ${metrics.interval} (USD)`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART.cost} stopOpacity={0.25} />
              <stop offset="100%" stopColor={CHART.cost} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis dataKey="label" {...AXIS_PROPS} interval="preserveStartEnd" minTickGap={32} />
          <YAxis
            {...AXIS_PROPS}
            tickFormatter={(v: number) => (v >= 0.01 || v === 0 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`)}
            width={64}
          />
          <Tooltip content={<DarkTooltip money />} cursor={{ stroke: CHART.axis, strokeWidth: 1 }} />
          <Area
            type="monotone"
            dataKey="cost"
            name="cost"
            stroke={CHART.cost}
            strokeWidth={2}
            fill="url(#costFill)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
