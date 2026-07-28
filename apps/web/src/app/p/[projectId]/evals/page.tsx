"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listDatasets, listEvalRuns } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Dataset, EvalRun } from "@/lib/types";
import { RunProgress, RunStatus } from "@/components/evals/RunStatus";
import { ErrorNote, Spinner } from "@/components/ui";

const GRID =
  "grid grid-cols-[28px_minmax(160px,1fr)_minmax(120px,0.7fr)_110px_minmax(140px,0.8fr)_150px] items-center gap-x-3";

export default function EvalsPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [datasets, setDatasets] = useState<Map<string, Dataset>>(new Map());
  const [error, setError] = useState<string | null>(null);
  // Comparison is paired, so exactly two runs — selecting a third drops the
  // oldest rather than silently refusing the click.
  const [selected, setSelected] = useState<string[]>([]);

  const toggle = (id: string) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-2),
    );

  const [a, b] = selected;
  const selectedRuns = selected.map((id) => runs?.find((r) => r.id === id));
  // Runs over different datasets have no items in common to pair on; the API
  // refuses it, so the button does too rather than leading somewhere that 400s.
  const sameDataset =
    selectedRuns.length === 2 &&
    selectedRuns[0] &&
    selectedRuns[1] &&
    selectedRuns[0].dataset_id === selectedRuns[1].dataset_id;

  const load = () => {
    setError(null);
    Promise.all([listEvalRuns(projectId), listDatasets(projectId)])
      .then(([r, d]) => {
        setRuns(r);
        setDatasets(new Map(d.map((x) => [x.id, x])));
      })
      .catch(() => setError("Couldn't load eval runs. Check that the API is running."));
  };

  useEffect(load, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live-ish list: refresh while anything is queued or running.
  useEffect(() => {
    if (!runs?.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [runs]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Evals</h1>
          {runs && <span className="font-mono text-2xs text-ink-faint">{runs.length} runs</span>}
        </div>
        <Link href={`/p/${projectId}/evals/new`} className="btn-ember">
          New eval run
        </Link>
      </header>

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      {runs === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : runs.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="max-w-md text-center">
            <h2 className="font-display text-base font-semibold">No runs yet</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
              An eval run scores a dataset with the graders you pick — from
              exact-match checks to LLM judges. Build a dataset first, then
              start a run here.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <div
            className={`${GRID} border-b border-line px-4 py-2 font-mono text-2xs uppercase tracking-wider text-ink-faint`}
          >
            <span className="sr-only">Select</span>
            <span aria-hidden />
            <span>Run</span>
            <span>Dataset</span>
            <span>Status</span>
            <span>Progress</span>
            <span className="text-right">Created</span>
          </div>
          {runs.map((run) => (
            <div
              key={run.id}
              className={`${GRID} border-b border-line/50 px-4 py-2.5 transition-colors duration-150 hover:bg-iron-850`}
            >
              <input
                type="checkbox"
                checked={selected.includes(run.id)}
                onChange={() => toggle(run.id)}
                aria-label={`Select ${run.name} for comparison`}
                className="h-3.5 w-3.5 cursor-pointer accent-ember"
              />
              <Link
                href={`/p/${projectId}/evals/${run.id}`}
                className="min-w-0 truncate text-[13px] text-ink hover:underline"
              >
                {run.name}
              </Link>
              <span className="min-w-0 truncate font-mono text-2xs text-ink-muted">
                {datasets.get(run.dataset_id)?.name ?? "—"}
              </span>
              <RunStatus status={run.status} />
              <RunProgress progress={run.progress} total={run.total} status={run.status} />
              <span className="text-right font-mono text-2xs text-ink-muted">
                {formatDateTime(run.created_at)}
              </span>
            </div>
          ))}
        </div>
      )}

      {selected.length > 0 && (
        <div
          role="region"
          aria-label="Comparison selection"
          className="flex flex-wrap items-center gap-3 border-t border-line bg-iron-900 px-4 py-2.5"
        >
          <span className="font-mono text-2xs text-ink-muted">
            {selected.length} of 2 runs selected
          </span>
          {selected.length === 2 && !sameDataset && (
            <span className="font-mono text-2xs text-status-warn">
              these runs scored different datasets — there are no shared items to pair
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button type="button" onClick={() => setSelected([])} className="btn-ghost h-7 px-2 text-2xs">
              Clear
            </button>
            {selected.length === 2 && sameDataset ? (
              <Link href={`/p/${projectId}/evals/compare?a=${a}&b=${b}`} className="btn-ember h-7 px-3 text-2xs">
                Compare
              </Link>
            ) : (
              <button type="button" disabled className="btn-ember h-7 px-3 text-2xs opacity-40">
                Compare
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
