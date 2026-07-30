"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, listTraces, type TraceFilters } from "@/lib/api";
import type { TraceSummary } from "@/lib/types";
import { DEFAULT_FILTERS, FilterBar, rangeToSince, type Filters } from "@/components/FilterBar";
import { TracesTable } from "@/components/TracesTable";
import { EmptyTraces } from "@/components/EmptyTraces";
import { AddToDataset } from "@/components/evals/AddToDataset";
import { ErrorNote, Spinner } from "@/components/ui";

function toApiFilters(f: Filters): TraceFilters {
  return {
    q: f.q.trim().length >= 3 ? f.q.trim() : undefined,
    status: f.status || undefined,
    name: f.name.trim() || undefined,
    model: f.model.trim() || undefined,
    feedback: f.feedback || undefined,
    since: rangeToSince(f.range),
    metadataKey: f.metadataKey.trim() || undefined,
    metadataValue: f.metadataValue.trim() || undefined,
    limit: 50,
  };
}

export default function TracesPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Monotonic token: a stale response from an abandoned filter set must not land.
  const fetchSeq = useRef(0);

  const fetchFirstPage = useCallback(
    async (f: Filters, quiet = false) => {
      const seq = ++fetchSeq.current;
      if (!quiet) setTraces(null);
      setError(null);
      try {
        const page = await listTraces(projectId, toApiFilters(f));
        if (seq !== fetchSeq.current) return;
        setTraces(page.items);
        setCursor(page.next_cursor);
      } catch (err) {
        if (seq !== fetchSeq.current) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Couldn't reach the API. Check that it's running.",
        );
        if (!quiet) setTraces([]);
      }
    },
    [projectId],
  );

  useEffect(() => {
    fetchFirstPage(filters);
    setSelected(new Set()); // a new filter set invalidates the old selection
  }, [filters, fetchFirstPage]);

  // 10 s auto-refresh: re-pulls the first page quietly; deeper pages reload on demand.
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(() => fetchFirstPage(filters, true), 10_000);
    return () => clearInterval(t);
  }, [autoRefresh, filters, fetchFirstPage]);

  async function loadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    const seq = fetchSeq.current;
    try {
      const page = await listTraces(projectId, { ...toApiFilters(filters), cursor });
      if (seq !== fetchSeq.current) return;
      setTraces((prev) => [...(prev ?? []), ...page.items]);
      setCursor(page.next_cursor);
    } catch {
      // keep what we have; the button stays for a retry
    } finally {
      setLoadingMore(false);
    }
  }

  const isFiltered =
    JSON.stringify({ ...filters, range: "" }) !== JSON.stringify({ ...DEFAULT_FILTERS, range: "" });

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <div className="mb-3 flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Traces</h1>
          {traces && (
            <span className="font-mono text-2xs text-ink-faint">
              {traces.length}
              {cursor ? "+" : ""} in view
            </span>
          )}
        </div>
        <FilterBar
          value={filters}
          onChange={setFilters}
          autoRefresh={autoRefresh}
          onAutoRefresh={setAutoRefresh}
        />
      </header>

      {/* bulk action bar — appears only when something is selected */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-b border-ember/30 bg-ember/5 px-4 py-2">
          <span className="font-mono text-2xs text-ember">
            {selected.size} selected
          </span>
          <AddToDataset
            projectId={projectId}
            traceIds={Array.from(selected)}
            onDone={() => setSelected(new Set())}
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
          <ErrorNote message={error} onRetry={() => fetchFirstPage(filters)} />
        </div>
      )}

      {traces === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : traces.length === 0 && !isFiltered && !error ? (
        <EmptyTraces projectId={projectId} />
      ) : traces.length === 0 ? (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-[13px] text-ink-muted">
            Nothing matches these filters. Widen the time range or clear them.
          </p>
        </div>
      ) : (
        <TracesTable
          traces={traces}
          hasMore={cursor !== null}
          loadingMore={loadingMore}
          onLoadMore={loadMore}
          selected={selected}
          onToggle={(id) =>
            setSelected((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
          onToggleAll={() =>
            setSelected((prev) =>
              prev.size === traces.length ? new Set() : new Set(traces.map((t) => t.id)),
            )
          }
        />
      )}
    </div>
  );
}
