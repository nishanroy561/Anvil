"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useRouter } from "next/navigation";
import { useRef } from "react";
import { formatAgo, formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import type { TraceSummary } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";
import { Spinner } from "./ui";

const ROW_H = 36;

const GRID =
  "grid grid-cols-[92px_minmax(160px,1.4fr)_92px_minmax(120px,1fr)_84px_100px_90px_72px] items-center gap-x-3 px-4";

/**
 * Virtualized list — only visible rows hit the DOM, so thousands of loaded
 * rows stay smooth (plan §2). Rows are buttons: keyboard-activatable, and the
 * whole row is the click target.
 */
export function TracesTable({
  traces,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  traces: TraceSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* header */}
      <div
        className={`${GRID} h-8 border-b border-line font-mono text-2xs uppercase tracking-wider text-ink-faint`}
      >
        <span>Time</span>
        <span>Name</span>
        <span>Status</span>
        <span>Model</span>
        <span className="text-right">Duration</span>
        <span className="text-right">Tokens in/out</span>
        <span className="text-right">Cost</span>
        <span className="text-right">Fb</span>
      </div>

      {/* rows */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((row) => {
            const t = traces[row.index];
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => router.push(`/traces/${t.id}`)}
                className={`${GRID} absolute left-0 top-0 w-full cursor-pointer border-b border-line/50 text-left transition-colors duration-150 hover:bg-iron-850`}
                style={{ height: ROW_H, transform: `translateY(${row.start}px)` }}
              >
                <span
                  className="font-mono text-2xs text-ink-muted"
                  title={new Date(t.started_at).toISOString()}
                >
                  {formatTime(t.started_at)}
                  <span className="ml-1 text-ink-faint">{formatAgo(t.started_at)}</span>
                </span>
                <span className="truncate text-[13px] text-ink">{t.name}</span>
                <StatusBadge status={t.status} />
                <span className="truncate font-mono text-2xs text-ink-muted">{t.model ?? "—"}</span>
                <span className="text-right font-mono text-2xs text-ink">
                  {formatDuration(t.latency_ms)}
                </span>
                <span className="text-right font-mono text-2xs text-ink-muted">
                  {formatTokens(t.total_tokens_in)}/{formatTokens(t.total_tokens_out)}
                </span>
                <span className="text-right font-mono text-2xs text-ink">
                  {formatCost(t.total_cost_usd)}
                </span>
                {/* feedback lands in Phase 9; the column exists so the layout doesn't shift */}
                <span className="text-right font-mono text-2xs text-ink-faint">—</span>
              </button>
            );
          })}
        </div>

        {hasMore && (
          <div className="flex justify-center py-3">
            <button type="button" onClick={onLoadMore} disabled={loadingMore} className="btn-ghost">
              {loadingMore ? <Spinner /> : "Load more"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
