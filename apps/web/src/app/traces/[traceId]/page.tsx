"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError, getTrace } from "@/lib/api";
import { formatCost, formatDateTime, formatDuration, formatTokens, shortId } from "@/lib/format";
import type { TraceDetail } from "@/lib/types";
import { FeedbackBadge, FeedbackPanel } from "@/components/Feedback";
import { StatusBadge } from "@/components/StatusBadge";
import { Waterfall } from "@/components/Waterfall";
import { SpanDetail } from "@/components/SpanDetail";
import { JsonView } from "@/components/JsonView";
import { CopyButton, ErrorNote, Spinner } from "@/components/ui";
import { Wordmark } from "@/components/Brand";
import { AddToDataset } from "@/components/evals/AddToDataset";

function HeaderStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="font-mono text-2xs uppercase tracking-wider text-ink-faint">{label}</span>
      <span className="font-mono text-[13px] text-ink">{value}</span>
    </div>
  );
}

export default function TracePage({ params }: { params: { traceId: string } }) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showTraceIO, setShowTraceIO] = useState(false);

  const load = () => {
    setError(null);
    getTrace(params.traceId)
      .then(setTrace)
      .catch((err) =>
        setError(
          err instanceof ApiError && err.status === 404
            ? "This trace doesn't exist, or belongs to another organization."
            : "Couldn't load the trace. Check that the API is running.",
        ),
      );
  };

  useEffect(load, [params.traceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedSpan = useMemo(
    () => trace?.spans.find((s) => s.id === selectedId) ?? null,
    [trace, selectedId],
  );

  const models = useMemo(() => {
    if (!trace) return "—";
    const set = new Set<string>();
    if (trace.model) set.add(trace.model);
    for (const s of trace.spans) {
      const m = s.attributes?.model;
      if (typeof m === "string") set.add(m);
    }
    return set.size ? Array.from(set).join(", ") : "—";
  }, [trace]);

  if (error) {
    return (
      <main className="flex min-h-screen flex-col">
        <TopBar projectId={null} />
        <div className="mx-auto mt-16 w-full max-w-md">
          <ErrorNote message={error} onRetry={load} />
        </div>
      </main>
    );
  }

  if (!trace) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <Spinner />
      </main>
    );
  }

  return (
    <main className="flex h-screen flex-col">
      <TopBar projectId={trace.project_id} />

      {/* trace header */}
      <header className="border-b border-line px-4 py-3">
        <div className="flex items-center gap-3">
          <h1 className="min-w-0 truncate font-display text-base font-semibold tracking-tight">
            {trace.name}
          </h1>
          <StatusBadge status={trace.status} />
          {/* Beside the status on purpose: "the request succeeded" and "the user
              hated the answer" are both facts about this trace, and the pair
              side by side is the whole argument for grading output. */}
          <FeedbackBadge counts={trace.feedback} />
          <span className="font-mono text-2xs text-ink-faint" title={trace.id}>
            {shortId(trace.id)}
          </span>
          <CopyButton value={trace.id} label="Copy trace id" />

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowTraceIO((v) => !v)}
              aria-pressed={showTraceIO}
              className="btn-ghost"
            >
              {showTraceIO ? "Hide" : "Show"} trace I/O
            </button>
            <AddToDataset projectId={trace.project_id} traceIds={[trace.id]} />
          </div>
        </div>

        <div className="mt-2.5 flex flex-wrap items-end gap-x-8 gap-y-2">
          <HeaderStat label="started" value={formatDateTime(trace.started_at)} />
          <HeaderStat label="latency" value={formatDuration(trace.latency_ms)} />
          <HeaderStat
            label="tokens in/out"
            value={`${formatTokens(trace.total_tokens_in)} / ${formatTokens(trace.total_tokens_out)}`}
          />
          <HeaderStat label="cost" value={formatCost(trace.total_cost_usd)} />
          <HeaderStat label="model(s)" value={models} />
          {trace.session_id && <HeaderStat label="session" value={trace.session_id} />}
          {trace.user_ref && <HeaderStat label="user" value={trace.user_ref} />}
        </div>

        {showTraceIO && (
          <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded-md border border-line bg-iron-900 p-3">
              <h3 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
                Trace input
              </h3>
              <div className="max-h-48 overflow-y-auto">
                <JsonView value={trace.input} />
              </div>
            </div>
            <div className="rounded-md border border-line bg-iron-900 p-3">
              <h3 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
                Trace output
              </h3>
              <div className="max-h-48 overflow-y-auto">
                <JsonView value={trace.output} />
              </div>
            </div>
          </div>
        )}
      </header>

      {/* What the users said, above the waterfall: the complaint is why anyone
          opened this trace, so it should not be below 100 spans of timing. */}
      <FeedbackPanel events={trace.feedback_events} />

      {/* waterfall + span panel */}
      <div className="flex min-h-0 flex-1">
        <Waterfall trace={trace} selectedId={selectedId} onSelect={setSelectedId} />

        <aside
          className="w-[380px] shrink-0 border-l border-line bg-iron-900"
          aria-label="Span details"
        >
          {selectedSpan ? (
            <SpanDetail span={selectedSpan} />
          ) : (
            <div className="flex h-full items-center justify-center p-6">
              <p className="text-center text-[13px] leading-relaxed text-ink-muted">
                Select a span to inspect its
                <br />
                input, output, and timing.
              </p>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

function TopBar({ projectId }: { projectId: string | null }) {
  return (
    <div className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-iron-900 px-4">
      <Link href="/" aria-label="Anvil home">
        <Wordmark />
      </Link>
      {projectId && (
        <Link
          href={`/p/${projectId}/traces`}
          className="flex items-center gap-1 text-[13px] text-ink-muted transition-colors duration-150 hover:text-ink"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
            <path d="M10 4 6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          All traces
        </Link>
      )}
    </div>
  );
}
