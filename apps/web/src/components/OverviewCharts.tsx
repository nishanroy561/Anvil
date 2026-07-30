"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
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
  // Feedback is a *diverging* pair, not two categories: downvotes and upvotes are
  // poles either side of a neutral zero, and the chart plots them that way.
  //
  // Downvotes are amber, not the error red. "The request failed" and "the answer
  // was wrong" are different problems — a drifted trace still returns `ok` — and
  // reusing the error hue would erase the distinction the whole product is built
  // on. Upvotes are a deeper green than CHART.ok because the pair has to clear
  // colour-blind separation from the amber: #039855 vs #B54708 scores ΔE 7.5
  // (deutan), inside the 6-8 band that needs an excuse; #047857 scores 9.3
  // (protan) and needs none. Verified with the dataviz palette validator.
  down: "#B54708",
  up: "#047857",
};

interface BucketRow {
  label: string;
  ok: number;
  errors: number;
  cost: number;
  /** Plotted below the axis, so the sign is the encoding, not just the hue. */
  down: number;
  up: number;
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
      down: -b.feedback_negative,
      up: b.feedback_positive,
    };
  });
}

function DarkTooltip({
  active,
  payload,
  label,
  money,
  signed,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color?: string }[];
  label?: string;
  money?: boolean;
  /** Values plotted with a sign for direction; show the magnitude. */
  signed?: boolean;
}) {
  if (!active || !payload?.length) return null;
  const rows = signed ? payload.filter((p) => p.value !== 0) : payload;
  if (rows.length === 0) return null;
  return (
    <div className="rounded-md border border-line bg-iron-850 px-2.5 py-1.5 font-mono text-2xs shadow-panel">
      <p className="mb-1 text-ink-faint">{label}</p>
      {rows.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5 text-ink">
          <span className="h-1.5 w-1.5 rounded-sm" style={{ background: p.color }} />
          {p.name}: {money ? formatCost(p.value) : signed ? Math.abs(p.value) : p.value}
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

/**
 * Votes per bucket, diverging around zero: downvotes hang below the axis, upvotes
 * rise above it.
 *
 * Its own chart rather than a series on the traces bars, for a reason the shared
 * axis makes unavoidable: traffic is counted in hundreds and votes in single
 * digits, so on one linear scale the feedback series is a flat line at the
 * baseline. Two measures of different magnitude get two charts — never a second
 * y-axis.
 *
 * The sign carries the meaning, so hue is the *second* encoding here, not the
 * first. That is also what makes the amber/green pair legal at ΔE 9.3.
 */
export function FeedbackChart({ metrics }: { metrics: MetricsOverview }) {
  const rows = toRows(metrics);
  const votes = metrics.total_feedback_negative + metrics.total_feedback_positive;

  // A **symmetric** domain with mirrored ticks, which is what makes the
  // magnitude labels honest. Left to auto-scale, the axis came out [-18, +6]:
  // zero sat near the top, and the two ticks either side of it both read "6" —
  // one meaning six upvotes and the other six downvotes, with nothing on screen
  // to tell them apart. Mirroring makes the repeat expected instead of wrong,
  // the way a population pyramid reads.
  const peak = rows.reduce((m, r) => Math.max(m, r.up, -r.down), 0);
  const bound = Math.max(4, Math.ceil(peak / 4) * 4);
  const ticks = [-bound, -bound / 2, 0, bound / 2, bound];

  return (
    <ChartFrame
      title={`End-user feedback / ${metrics.interval}`}
      legend={[
        { label: "down", color: CHART.down },
        { label: "up", color: CHART.up },
      ]}
    >
      {votes === 0 ? (
        <div className="flex h-full items-center justify-center">
          <p className="max-w-xs text-center text-2xs leading-relaxed text-ink-muted">
            No votes in this window. Call{" "}
            <code className="font-mono text-ink-faint">anvil.feedback(trace_id, -1)</code> when a
            user rejects an answer, and complaints show up here and in the inbox.
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          {/*
            Not stacked, and not `stackOffset="sign"`. That combination looked
            right and rendered nothing: with an explicit symmetric domain,
            recharts emitted the negative bars as `<rect height="-44">`, and SVG
            silently drops a rect with negative height. The amber bars were
            *absent*, not short — which a screenshot at page scale reads as "the
            data is tiny" rather than "the mark is missing".

            Two plain bars need no stack: a negative value already draws downward
            from zero, and grouping them makes each separately hoverable.
          */}
          <BarChart data={rows} margin={{ top: 4, right: 4, left: -18, bottom: 0 }} barCategoryGap="30%" barGap={2}>
            <CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis dataKey="label" {...AXIS_PROPS} interval="preserveStartEnd" minTickGap={32} />
            <YAxis
              {...AXIS_PROPS}
              domain={[-bound, bound]}
              ticks={ticks}
              allowDecimals={false}
              // Magnitudes, not signed values: the direction is carried by which
              // side of zero the bar is on, so "-3 downvotes" would be a double
              // negative. Safe only because the domain above is symmetric.
              tickFormatter={(v: number) => String(Math.abs(v))}
            />
            {/* The zero line is the reference the whole form depends on, so it is
                drawn darker than the grid rather than left to blend into it. */}
            <ReferenceLine y={0} stroke={CHART.axis} strokeWidth={1} />
            <Tooltip content={<DarkTooltip signed />} cursor={{ fill: "rgba(19,19,22,0.04)" }} />
            {/* Capped width. Feedback is sparse — a window often holds two or
                three populated buckets — and without a cap recharts spreads them
                to fill the container, turning a 13-vote bar into a slab wider
                than it is tall. */}
            <Bar dataKey="down" name="down" fill={CHART.down} radius={[0, 0, 3, 3]} maxBarSize={26} />
            <Bar dataKey="up" name="up" fill={CHART.up} radius={[3, 3, 0, 0]} maxBarSize={26} />
          </BarChart>
        </ResponsiveContainer>
      )}
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
