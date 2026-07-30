"use client";

import clsx from "clsx";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRouter } from "next/navigation";
import { useRef } from "react";
import { formatAgo, formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import type { TraceSummary } from "@/lib/types";
import { FeedbackBadge } from "./Feedback";
import { StatusBadge } from "./StatusBadge";
import { Spinner } from "./ui";

const ROW_H = 36;

// The feedback column is wider than the old placeholder: it holds two glyph+count
// pairs, and a trace with both a 👎 and a 👍 is exactly the ambiguous case worth
// seeing at a glance.
const GRID =
  "grid grid-cols-[28px_92px_minmax(150px,1.4fr)_92px_minmax(120px,1fr)_84px_100px_90px_76px] items-center gap-x-3 px-4";

/**
 * Virtualized list — only visible rows hit the DOM, so thousands of loaded
 * rows stay smooth (plan §2). The checkbox column feeds bulk "Add to dataset"
 * (plan/evaluation.md §1); row click still opens the trace.
 */
export function TracesTable({
  traces,
  hasMore,
  loadingMore,
  onLoadMore,
  selected,
  onToggle,
  onToggleAll,
}: {
  traces: TraceSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  selected: Set<string>;
  onToggle: (traceId: string) => void;
  onToggleAll: () => void;
}) {
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  });

  const allSelected = traces.length > 0 && selected.size === traces.length;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* header */}
      <div
        className={`${GRID} h-8 border-b border-line font-mono text-2xs uppercase tracking-wider text-ink-faint`}
      >
        <input
          type="checkbox"
          checked={allSelected}
          onChange={onToggleAll}
          aria-label={allSelected ? "Clear selection" : "Select all loaded traces"}
          className="h-3.5 w-3.5 cursor-pointer accent-[#3B76FF]"
        />
        <span>Time</span>
        <span>Name</span>
        <span>Status</span>
        <span>Model</span>
        <span className="text-right">Duration</span>
        <span className="text-right">Tokens in/out</span>
        <span className="text-right">Cost</span>
        <span className="text-right">Feedback</span>
      </div>

      {/* rows */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((row) => {
            const t = traces[row.index];
            const isSelected = selected.has(t.id);
            return (
              <div
                key={t.id}
                className={clsx(
                  `${GRID} absolute left-0 top-0 w-full border-b border-line/50 transition-colors duration-150`,
                  isSelected ? "bg-ember/5" : "hover:bg-iron-850",
                )}
                style={{ height: ROW_H, transform: `translateY(${row.start}px)` }}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => onToggle(t.id)}
                  aria-label={`Select trace ${t.name}`}
                  className="h-3.5 w-3.5 cursor-pointer accent-[#3B76FF]"
                />
                <button
                  type="button"
                  onClick={() => router.push(`/traces/${t.id}`)}
                  className="contents cursor-pointer text-left"
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
                  <span className="flex justify-end">
                    <FeedbackBadge counts={t.feedback} />
                  </span>
                </button>
              </div>
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
