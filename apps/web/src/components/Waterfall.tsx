"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { formatDuration } from "@/lib/format";
import type { TraceDetail } from "@/lib/types";
import { buildWaterfall, rulerTicks, type WaterfallRow } from "@/lib/waterfall";
import { SPAN_COLORS } from "./spanColors";

const ROW_H = 28;
const TREE_W = 300; // px, the left tree column — long span names truncate, never bleed

/**
 * The centerpiece (plan/dashboard.md §3): tree and timeline share rows, like a
 * machinist's drawing — labels on the left, ruled time on the right. Pure
 * CSS/flex positioning; percentages of the trace extent place every bar.
 */
export function Waterfall({
  trace,
  selectedId,
  onSelect,
}: {
  trace: TraceDetail;
  selectedId: string | null;
  onSelect: (spanId: string) => void;
}) {
  const model = useMemo(() => buildWaterfall(trace), [trace]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Depth-first walk skipping collapsed subtrees → visible rows in order.
  const visible = useMemo(() => {
    const out: WaterfallRow[] = [];
    const walk = (rows: WaterfallRow[]) => {
      for (const r of rows) {
        out.push(r);
        if (!collapsed.has(r.span.id)) walk(r.children);
      }
    };
    walk(model.roots);
    return out;
  }, [model, collapsed]);

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const ticks = rulerTicks(model.totalMs);
  const traceOpen = trace.status === "in_progress" || trace.status === "abandoned";

  if (model.rows.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-[13px] text-ink-muted">
          This trace has no spans{traceOpen ? " yet — it may still be running." : "."}
        </p>
      </div>
    );
  }

  return (
    <div className="min-w-0 flex-1 overflow-auto">
      <div className="min-w-[720px]">
        {/* ruler */}
        <div className="sticky top-0 z-10 flex border-b border-line bg-iron-950">
          <div
            className="shrink-0 border-r border-line px-3 py-1.5 font-mono text-2xs uppercase tracking-wider text-ink-faint"
            style={{ width: TREE_W }}
          >
            {model.rows.length} spans
          </div>
          <div className="relative h-7 flex-1">
            {ticks.map((t) => (
              <span
                key={t.atPct}
                className="absolute top-0 flex h-full flex-col justify-between"
                style={{ left: `${t.atPct}%` }}
              >
                <span
                  className={clsx(
                    "font-mono text-2xs text-ink-faint",
                    t.atPct === 100 ? "-translate-x-full pr-1" : "pl-1",
                  )}
                >
                  {t.label}
                </span>
                <span className="h-1.5 w-px bg-line-strong" aria-hidden="true" />
              </span>
            ))}
          </div>
        </div>

        {/* rows */}
        <div role="list" aria-label="Span waterfall">
          {visible.map((row) => {
            const s = row.span;
            const leftPct = (row.startMs / model.totalMs) * 100;
            const widthPct = ((row.endMs - row.startMs) / model.totalMs) * 100;
            const color = SPAN_COLORS[s.type] ?? SPAN_COLORS.custom;
            const isHot = s.id === model.hottestSpanId;
            const isSelected = s.id === selectedId;
            const hasChildren = row.children.length > 0;
            const isCollapsed = collapsed.has(s.id);

            return (
              <div
                key={s.id}
                role="listitem"
                className={clsx(
                  "flex border-b border-line/40 transition-colors duration-150",
                  isSelected ? "bg-iron-800" : "hover:bg-iron-850",
                )}
                style={{ height: ROW_H }}
              >
                {/* tree cell */}
                <div
                  className="flex shrink-0 items-center gap-1 overflow-hidden border-r border-line pr-2"
                  style={{ width: TREE_W, paddingLeft: 8 + row.depth * 14 }}
                >
                  {hasChildren ? (
                    <button
                      type="button"
                      onClick={() => toggle(s.id)}
                      aria-label={isCollapsed ? `Expand ${s.name}` : `Collapse ${s.name}`}
                      aria-expanded={!isCollapsed}
                      className="flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded text-ink-faint hover:text-ink"
                    >
                      <svg
                        viewBox="0 0 16 16"
                        className={clsx(
                          "h-3 w-3 transition-transform duration-150",
                          !isCollapsed && "rotate-90",
                        )}
                        fill="none"
                        aria-hidden="true"
                      >
                        <path d="m6 4 4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  ) : (
                    <span className="w-4 shrink-0" aria-hidden="true" />
                  )}
                  <button
                    type="button"
                    onClick={() => onSelect(s.id)}
                    className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-left"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-sm"
                      style={{ background: color }}
                      aria-hidden="true"
                    />
                    <span
                      className={clsx(
                        "truncate text-[13px]",
                        s.status === "error" ? "text-status-error" : "text-ink",
                      )}
                    >
                      {s.name}
                    </span>
                    {row.orphan && (
                      <span
                        className="shrink-0 rounded border border-line px-1 font-mono text-2xs text-ink-faint"
                        title="Parent span was never received — shown at root level"
                      >
                        orphan
                      </span>
                    )}
                  </button>
                </div>

                {/* timeline cell */}
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  aria-label={`${s.name}, ${row.openEnded ? "still running" : formatDuration(row.endMs - row.startMs)}`}
                  className="relative min-w-0 flex-1 cursor-pointer"
                >
                  {/* faint grid lines under the bars */}
                  {ticks.slice(1, -1).map((t) => (
                    <span
                      key={t.atPct}
                      className="absolute inset-y-0 w-px bg-line/40"
                      style={{ left: `${t.atPct}%` }}
                      aria-hidden="true"
                    />
                  ))}

                  <span
                    className={clsx("absolute top-1/2 h-3.5 -translate-y-1/2 rounded-[3px]", isHot && "shadow-ember")}
                    style={{
                      left: `${leftPct}%`,
                      width: `max(${widthPct}%, 3px)`,
                      background: row.openEnded
                        ? `linear-gradient(90deg, ${color} 0%, transparent 96%)`
                        : color,
                      outline: isSelected ? "1px solid #131316" : isHot ? "1px solid #3B76FF" : undefined,
                      outlineOffset: 1,
                    }}
                  />

                  {/* duration label sits after the bar, flipping side near the right edge */}
                  <span
                    className={clsx(
                      "absolute top-1/2 -translate-y-1/2 whitespace-nowrap font-mono text-2xs",
                      isHot ? "font-medium text-ember" : "text-ink-muted",
                    )}
                    style={
                      leftPct + widthPct > 82
                        ? { right: `${Math.max(100 - leftPct, 2)}%`, paddingRight: 6 }
                        : { left: `${Math.min(leftPct + widthPct, 97)}%`, paddingLeft: 6 }
                    }
                  >
                    {row.openEnded
                      ? trace.status === "abandoned"
                        ? "abandoned"
                        : "still running…"
                      : formatDuration(row.endMs - row.startMs)}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
