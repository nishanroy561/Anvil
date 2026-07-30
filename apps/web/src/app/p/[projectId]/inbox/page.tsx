"use client";

import clsx from "clsx";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { failureInbox } from "@/lib/api";
import { formatAgo, formatCost, formatDateTime, formatDuration } from "@/lib/format";
import type { FailureInboxItem } from "@/lib/types";
import { AddToDataset } from "@/components/evals/AddToDataset";
import { FeedbackQuote, ThumbIcon } from "@/components/Feedback";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorNote, Spinner } from "@/components/ui";

const PAGE = 50;

/**
 * Failure inbox (plan/evaluation.md §7): thumbs-down traces that are not yet in
 * any dataset.
 *
 * The design decision worth defending is that **the quote leads the row**. Every
 * other list in this product is a grid of machine facts, because that is what
 * traces are. An inbox entry is different: someone typed a sentence explaining
 * what went wrong, and that sentence is the entire reason the row deserves
 * attention. So it is set in the body face at reading size, and the model,
 * latency and cost are demoted to mono microcopy underneath. A truncated cell in
 * a grid would bury the only thing here a human wrote.
 *
 * It is an inbox rather than a list because promoting an entry is what removes
 * it. The count going down is the product working.
 */

function InboxRow({
  item,
  selected,
  onToggle,
}: {
  item: FailureInboxItem;
  selected: boolean;
  onToggle: () => void;
}) {
  const { trace } = item;
  const downvotes = item.feedback.length;
  const [expanded, setExpanded] = useState(false);
  // The first vote is the headline; the rest are behind a disclosure so one
  // much-complained-about trace can't push everything else off the screen.
  const [lead, ...rest] = item.feedback;

  return (
    <li
      className={clsx(
        "rounded-lg border bg-iron-900 p-4 transition-colors duration-150",
        selected ? "border-ember/50 bg-ember/[0.04]" : "border-line hover:border-line-strong",
      )}
    >
      <div className="flex gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select the failure on trace ${trace.name}`}
          className="mt-1 h-3.5 w-3.5 shrink-0 cursor-pointer accent-[#3B76FF]"
        />

        <div className="min-w-0 flex-1">
          {/* The quote — the reason this row exists. */}
          <ul className="flex flex-col gap-2.5">
            <FeedbackQuote event={lead} />
            {expanded && rest.map((e) => <FeedbackQuote key={e.id} event={e} />)}
          </ul>

          {rest.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="mt-1.5 cursor-pointer font-mono text-2xs text-ember transition-colors duration-150 hover:underline"
            >
              {expanded
                ? "show less"
                : `${rest.length} more ${rest.length === 1 ? "downvote" : "downvotes"}`}
            </button>
          )}

          {/* Machine facts, demoted. */}
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 font-mono text-2xs text-ink-faint">
            <span className="inline-flex items-center gap-1 text-status-warn" title="Downvotes">
              <ThumbIcon down />
              {downvotes}
            </span>
            <span className="text-ink-muted">{trace.name}</span>
            <StatusBadge status={trace.status} />
            {trace.model && <span>{trace.model}</span>}
            <span>{formatDuration(trace.latency_ms)}</span>
            <span>{formatCost(trace.total_cost_usd)}</span>
            {/* "ran" is load-bearing: the quote above carries its own "2m ago"
                (when the vote arrived) and a bare second "9d ago" down here reads
                as a contradiction rather than as the trace's own age. */}
            <span title={formatDateTime(trace.started_at)}>ran {formatAgo(trace.started_at)}</span>
            <Link
              href={`/traces/${trace.id}`}
              className="ml-auto text-ember transition-colors duration-150 hover:underline"
            >
              Open trace &rsaquo;
            </Link>
          </div>
        </div>
      </div>
    </li>
  );
}

export default function InboxPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [items, setItems] = useState<FailureInboxItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(() => {
    setError(null);
    failureInbox(projectId, PAGE)
      .then((page) => {
        setItems(page.items);
        setTotal(page.total);
      })
      .catch(() => setError("Couldn't load the inbox. Check that the API is running."));
  }, [projectId]);

  useEffect(() => {
    load();
    setSelected(new Set());
  }, [load]);

  async function loadMore() {
    if (!items || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await failureInbox(projectId, PAGE, items.length);
      setItems((prev) => [...(prev ?? []), ...page.items]);
      setTotal(page.total);
    } catch {
      // keep what we have; the button stays for a retry
    } finally {
      setLoadingMore(false);
    }
  }

  const selectedIds = Array.from(selected);
  const allSelected = items !== null && items.length > 0 && selected.size === items.length;

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Failure inbox</h1>
          {items && (
            <span className="font-mono text-2xs text-ink-faint">
              {total} awaiting triage
            </span>
          )}
        </div>
        <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink-muted">
          Traces your end users marked with a thumbs-down that aren&apos;t in a dataset yet. Adding
          one to a dataset turns a complaint into a test case — and clears it from here.
        </p>
      </header>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-b border-ember/30 bg-ember/5 px-4 py-2">
          <span className="font-mono text-2xs text-ember">{selected.size} selected</span>
          <AddToDataset
            projectId={projectId}
            traceIds={selectedIds}
            onDone={() => {
              setSelected(new Set());
              // Promotion is exactly what removes these rows, so re-read rather
              // than filtering locally — the server owns which entries survive.
              load();
            }}
          />
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="cursor-pointer text-2xs text-ink-muted transition-colors duration-150 hover:text-ink"
          >
            clear
          </button>
        </div>
      )}

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      {items === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4">
          <div className="max-w-md text-center">
            <h2 className="font-display text-base font-semibold">Nothing to triage</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
              Downvoted traces land here the moment a user sends one, and leave when you add them
              to a dataset. An empty inbox means either nobody is complaining or you&apos;ve
              already turned every complaint into a test case.
            </p>
            <p className="mt-3 font-mono text-2xs text-ink-faint">
              Wire up the button:{" "}
              <code className="text-ink-muted">anvil.feedback(trace_id, -1)</code>
            </p>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="mb-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() =>
                setSelected(allSelected ? new Set() : new Set(items.map((i) => i.trace.id)))
              }
              className="cursor-pointer font-mono text-2xs text-ink-muted transition-colors duration-150 hover:text-ink"
            >
              {allSelected ? "clear selection" : `select all ${items.length} shown`}
            </button>
          </div>

          <ul className="flex flex-col gap-2.5">
            {items.map((item) => (
              <InboxRow
                key={item.trace.id}
                item={item}
                selected={selected.has(item.trace.id)}
                onToggle={() =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (next.has(item.trace.id)) next.delete(item.trace.id);
                    else next.add(item.trace.id);
                    return next;
                  })
                }
              />
            ))}
          </ul>

          {items.length < total && (
            <div className="flex justify-center py-4">
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="btn-ghost"
              >
                {loadingMore ? <Spinner /> : `Load ${Math.min(PAGE, total - items.length)} more`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
