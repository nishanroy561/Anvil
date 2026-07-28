"use client";

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { cancelEvalRun, getEvalResults, getEvalRun } from "@/lib/api";
import { formatCost, formatDuration } from "@/lib/format";
import type { EvalResult, EvalRun } from "@/lib/types";
import { JsonView } from "@/components/JsonView";
import { RunProgress, RunStatus } from "@/components/evals/RunStatus";
import { ErrorNote, Spinner } from "@/components/ui";

const LIVE = ["queued", "running"];

function scoreTone(score: number, threshold = 0.5): string {
  return score >= threshold ? "text-status-ok" : "text-status-error";
}

function SummaryCards({ run }: { run: EvalRun }) {
  const summary = run.summary;
  if (!summary) return null;
  const graders = Object.entries(summary.graders);
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {graders.map(([name, g]) => (
        <div key={name} className="rounded-lg border border-line bg-iron-900 p-3">
          <p className="truncate font-mono text-2xs uppercase tracking-wider text-ink-faint" title={name}>
            {name}
          </p>
          <p className="mt-1 font-mono text-xl font-medium tabular-nums text-ink">
            {g.mean === null ? "—" : g.mean.toFixed(2)}
          </p>
          <p className="mt-0.5 font-mono text-2xs text-ink-muted">
            {g.pass_rate === null ? "no scores" : `${Math.round(g.pass_rate * 100)}% pass`}
            {g.errored_items > 0 && (
              <span className="text-status-error"> · {g.errored_items} errored</span>
            )}
          </p>
          {(g.tautological_items ?? 0) > 0 && (
            // A mean built mostly from self-comparisons is not a measurement.
            // Saying so next to the number is the whole point.
            <p
              className="mt-1.5 font-mono text-2xs leading-snug text-status-warn"
              title="These items were graded against their own output, so the grader could only return a perfect score. Edit the expected outputs, or run in generate mode, to measure anything."
            >
              {g.tautological_items === g.scored_items
                ? "measures nothing — graded against itself"
                : `${g.tautological_items} of ${g.scored_items} graded against itself`}
            </p>
          )}
        </div>
      ))}
      <div className="rounded-lg border border-line bg-iron-900 p-3">
        <p className="font-mono text-2xs uppercase tracking-wider text-ink-faint">Run</p>
        <p className="mt-1 font-mono text-xl font-medium tabular-nums text-ink">
          {formatCost(summary.total_cost_usd)}
        </p>
        <p className="mt-0.5 font-mono text-2xs text-ink-muted">
          p95 {formatDuration(summary.p95_latency_ms)}
          {summary.failed_items > 0 && (
            <span className="text-status-error"> · {summary.failed_items} failed</span>
          )}
        </p>
      </div>
    </div>
  );
}

function ResultRow({ result, graderNames }: { result: EvalResult; graderNames: string[] }) {
  const [open, setOpen] = useState(false);
  const reasoning = result.judge_reasoning ?? {};
  // A *grader* failing is not the same as the *item* failing, and it is the
  // more common case: a provider 429 errors one grader while the item's other
  // graders score fine. Both have to open the row, or the provider's own
  // message is reachable only as a tooltip.
  const graderErrors = Object.entries(result.scores ?? {}).flatMap(([name, entry]) =>
    entry?.error ? [[name, entry.error] as const] : [],
  );
  // Scores that could only ever have come back perfect (ADR-0018). Surfaced on
  // the row as well as the summary, so a reader scanning the table sees which
  // individual 1.00s to discount.
  const tautological = Object.entries(result.scores ?? {}).flatMap(([name, entry]) =>
    entry?.tautological ? [name] : [],
  );
  const hasDetail =
    Object.keys(reasoning).length > 0 ||
    result.error !== null ||
    graderErrors.length > 0 ||
    tautological.length > 0;
  const toggle = () => setOpen((v) => !v);

  return (
    <>
      <tr
        className={clsx(
          "border-b border-line/50 align-top transition-colors duration-150",
          hasDetail && "cursor-pointer hover:bg-iron-850",
        )}
        onClick={hasDetail ? toggle : undefined}
        // Expanding is the only way to read a judge's reasoning or a grader
        // error, so it cannot be mouse-only.
        role={hasDetail ? "button" : undefined}
        tabIndex={hasDetail ? 0 : undefined}
        aria-expanded={hasDetail ? open : undefined}
        onKeyDown={
          hasDetail
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle();
                }
              }
            : undefined
        }
      >
        <td className="px-4 py-2">
          <pre className="max-h-16 overflow-hidden whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink-muted">
            {typeof result.output === "object" ? JSON.stringify(result.output) : String(result.output ?? "")}
          </pre>
        </td>
        {graderNames.map((name) => {
          const entry = result.scores?.[name];
          return (
            <td key={name} className="px-3 py-2 text-right font-mono text-2xs tabular-nums">
              {result.error ? (
                <span className="text-ink-faint">—</span>
              ) : entry === undefined ? (
                <span className="text-ink-faint">—</span>
              ) : "error" in entry && entry.error ? (
                <span className="text-status-error" title={entry.error}>
                  err
                </span>
              ) : entry.tautological ? (
                <span
                  className="text-status-warn"
                  title="Graded against its own output — this score could only have been perfect"
                >
                  {(entry.score ?? 0).toFixed(2)}
                  <span aria-hidden> *</span>
                  <span className="sr-only"> (graded against itself)</span>
                </span>
              ) : (
                <span className={scoreTone(entry.score ?? 0)}>{(entry.score ?? 0).toFixed(2)}</span>
              )}
            </td>
          );
        })}
        <td className="px-3 py-2 text-right font-mono text-2xs text-ink-muted">
          {formatDuration(result.latency_ms)}
        </td>
        <td className="px-3 py-2 text-right">
          {hasDetail && (
            <span className="font-mono text-2xs text-ink-faint">{open ? "▾" : "▸"}</span>
          )}
        </td>
      </tr>

      {open && (
        <tr className="border-b border-line/50 bg-iron-900">
          <td colSpan={graderNames.length + 3} className="px-4 py-3">
            {result.error && (
              <div className="mb-3">
                <h4 className="mb-1 font-mono text-2xs uppercase tracking-wider text-status-error">
                  Item error
                </h4>
                <pre className="whitespace-pre-wrap break-words font-mono text-2xs text-status-error">
                  {result.error}
                </pre>
              </div>
            )}
            {graderErrors.map(([grader, message]) => (
              <div key={grader} className="mb-3">
                <h4 className="mb-1 font-mono text-2xs uppercase tracking-wider text-status-error">
                  {grader} — grader error
                </h4>
                <pre className="whitespace-pre-wrap break-words font-mono text-2xs text-status-error">
                  {message}
                </pre>
              </div>
            ))}
            {tautological.length > 0 && (
              <div className="mb-3">
                <h4 className="mb-1 font-mono text-2xs uppercase tracking-wider text-status-warn">
                  {tautological.join(", ")} — graded against itself
                </h4>
                <p className="text-2xs leading-relaxed text-ink-muted">
                  This item&apos;s expected output is the same value as the output being
                  graded, so these graders could only return a perfect score. Edit the
                  expected output on the dataset to say what the answer{" "}
                  <em>should</em> be, or run the eval in generate mode against a target
                  model.
                </p>
              </div>
            )}
            {Object.entries(reasoning).map(([grader, text]) => (
              <div key={grader} className="mb-3 last:mb-0">
                <h4 className="mb-1 font-mono text-2xs uppercase tracking-wider text-ink-faint">
                  {grader} — judge reasoning
                </h4>
                <p className="text-2xs leading-relaxed text-ink">{text}</p>
              </div>
            ))}
            <div className="mt-2">
              <h4 className="mb-1 font-mono text-2xs uppercase tracking-wider text-ink-faint">
                Full output
              </h4>
              <div className="max-h-48 overflow-y-auto">
                <JsonView value={result.output} />
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function EvalRunPage({
  params,
}: {
  params: { projectId: string; runId: string };
}) {
  const { projectId, runId } = params;
  const [run, setRun] = useState<EvalRun | null>(null);
  const [results, setResults] = useState<EvalResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const load = () => {
    Promise.all([getEvalRun(runId), getEvalResults(runId)])
      .then(([r, page]) => {
        setRun(r);
        setResults(page.items);
        setError(null);
      })
      .catch(() => setError("Couldn't load the run. Check that the API is running."));
  };

  useEffect(load, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live progress: 2 s poll while the run is queued/running (plan §3 UI spec).
  useEffect(() => {
    if (!run || !LIVE.includes(run.status)) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const graderNames = useMemo(
    () => (run?.grader_config?.graders ?? []).map((g) => g.name),
    [run],
  );

  if (error && !run) {
    return (
      <div className="mx-auto mt-10 max-w-md px-4">
        <ErrorNote message={error} onRetry={load} />
      </div>
    );
  }

  if (!run) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <nav aria-label="Breadcrumb" className="mb-1 font-mono text-2xs text-ink-faint">
          <Link href={`/p/${projectId}/evals`} className="hover:text-ink">
            Evals
          </Link>
          <span className="mx-1">/</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">{run.name}</h1>
          <RunStatus status={run.status} />
          <span className="font-mono text-2xs text-ink-faint">
            {run.target_config?.mode === "generate"
              ? `generate · ${run.target_config.model}`
              : "re-score"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Link href={`/p/${projectId}/datasets/${run.dataset_id}`} className="btn-ghost">
              Dataset
            </Link>
            {LIVE.includes(run.status) && (
              <button
                type="button"
                disabled={cancelling}
                onClick={async () => {
                  setCancelling(true);
                  try {
                    await cancelEvalRun(runId);
                    load();
                  } finally {
                    setCancelling(false);
                  }
                }}
                className="btn-ghost hover:border-status-error/50 hover:text-status-error"
              >
                {cancelling ? <Spinner /> : "Cancel run"}
              </button>
            )}
          </div>
        </div>

        <div className="mt-3 max-w-xl">
          <RunProgress progress={run.progress} total={run.total} status={run.status} />
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        {run.summary && (
          <div className="border-b border-line px-4 py-3">
            <SummaryCards run={run} />
          </div>
        )}

        {results.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            {LIVE.includes(run.status) ? (
              <p className="flex items-center gap-2 text-[13px] text-ink-muted">
                <Spinner /> Waiting for the first scored items…
              </p>
            ) : (
              <p className="text-[13px] text-ink-muted">No results were recorded for this run.</p>
            )}
          </div>
        ) : (
          <table className="w-full min-w-[720px] border-collapse">
            <thead className="sticky top-0 z-10 bg-iron-950">
              <tr className="border-b border-line text-left font-mono text-2xs uppercase tracking-wider text-ink-faint">
                <th className="px-4 py-2 font-medium">Output</th>
                {graderNames.map((name) => (
                  <th key={name} className="w-24 px-3 py-2 text-right font-medium">
                    {name}
                  </th>
                ))}
                <th className="w-24 px-3 py-2 text-right font-medium">Latency</th>
                <th className="w-10 px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <ResultRow key={result.id} result={result} graderNames={graderNames} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
