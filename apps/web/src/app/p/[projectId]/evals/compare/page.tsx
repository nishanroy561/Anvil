"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { compareEvalRuns, getEvalResults, getEvalRun } from "@/lib/api";
import { formatCost, formatDuration } from "@/lib/format";
import type { EvalResult, EvalRun, GraderComparison, RunComparison } from "@/lib/types";
import { JsonView } from "@/components/JsonView";
import { ErrorNote, Spinner } from "@/components/ui";

/** 0.62 → "62%". Scores are 0..1 across every grader, so this always applies. */
function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function signed(value: number | null, digits = 2): string {
  if (value === null) return "—";
  const s = value.toFixed(digits);
  return value > 0 ? `+${s}` : s;
}

/**
 * The plain-language sentence, which is the whole point of the page: a reader
 * should not have to interpret a confidence interval to know whether they may
 * act on this. Inconclusive verdicts say so in words and never lead with a
 * percentage — ADR-0008 makes that a product requirement, not a style choice.
 */
function verdictSentence(g: GraderComparison, nameA: string, nameB: string): string {
  if (g.verdict === "inconclusive") {
    switch (g.reason) {
      case "no_paired_scores":
        return `${g.grader}: no items were scored by both runs, so there is nothing to compare.`;
      case "tautological":
        return `${g.grader}: both runs graded each output against itself, so neither number measured anything. Edit the dataset's expected outputs, or run against a target model.`;
      case "insufficient_data":
        return `${g.grader}: not enough data to call it — ${g.n} item${g.n === 1 ? "" : "s"} compared, and it takes at least 30 before a difference this size can be told apart from chance.`;
      default:
        return `${g.grader}: not enough data to call it — the range of plausible values (${signed(g.ci?.[0] ?? null)} to ${signed(g.ci?.[1] ?? null)}) still includes "no change".`;
    }
  }
  const direction = g.verdict === "improved" ? "raised" : "lowered";
  return `${nameB} ${direction} ${g.grader} ${pct(g.mean_a)} → ${pct(g.mean_b)} vs ${nameA} — significant, CI ${signed(g.ci?.[0] ?? null)} to ${signed(g.ci?.[1] ?? null)}.`;
}

const VERDICT_STYLE: Record<GraderComparison["verdict"], string> = {
  improved: "border-status-ok/40 bg-status-ok/5 text-status-ok",
  regressed: "border-status-error/40 bg-status-error/5 text-status-error",
  inconclusive: "border-line bg-iron-900 text-ink-muted",
};

function VerdictChip({ g }: { g: GraderComparison }) {
  const label =
    g.verdict === "inconclusive" ? "inconclusive" : g.verdict === "improved" ? "improved" : "regressed";
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 font-mono text-2xs font-medium ${VERDICT_STYLE[g.verdict]}`}
    >
      {label}
    </span>
  );
}

function GraderTable({ comparison }: { comparison: RunComparison }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] border-collapse">
        <thead>
          <tr className="border-b border-line text-left font-mono text-2xs uppercase tracking-wider text-ink-faint">
            <th className="px-4 py-2 font-medium">Grader</th>
            <th className="px-3 py-2 text-right font-medium">A</th>
            <th className="px-3 py-2 text-right font-medium">B</th>
            <th className="px-3 py-2 text-right font-medium">Delta</th>
            <th className="px-3 py-2 text-right font-medium" title="95% confidence interval of the mean paired delta">
              95% CI
            </th>
            <th className="px-3 py-2 text-right font-medium" title="Exact McNemar, binary graders only">
              p
            </th>
            <th className="px-3 py-2 text-right font-medium">n</th>
            <th className="px-3 py-2 font-medium">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {comparison.graders.map((g) => (
            <tr key={g.grader} className="border-b border-line/50 align-top">
              <td className="px-4 py-2.5">
                <span className="font-mono text-2xs text-ink">{g.grader}</span>
                {g.tautological_pairs > 0 && (
                  <p className="mt-0.5 font-mono text-2xs text-status-warn">
                    {g.tautological_pairs} of {g.n} graded against itself
                  </p>
                )}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums text-ink-muted">
                {g.mean_a === null ? "—" : g.mean_a.toFixed(2)}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums text-ink-muted">
                {g.mean_b === null ? "—" : g.mean_b.toFixed(2)}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums">
                <span
                  className={
                    g.delta === null || g.delta === 0
                      ? "text-ink-muted"
                      : g.delta > 0
                        ? "text-status-ok"
                        : "text-status-error"
                  }
                >
                  {signed(g.delta)}
                </span>
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums text-ink-muted">
                {g.ci ? `${signed(g.ci[0])} … ${signed(g.ci[1])}` : "—"}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums text-ink-muted">
                {g.p_value === null ? "—" : g.p_value < 0.001 ? g.p_value.toExponential(1) : g.p_value.toFixed(3)}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-2xs tabular-nums text-ink-muted">
                {g.n}
              </td>
              <td className="px-3 py-2.5">
                <VerdictChip g={g} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object" && value !== null && "value" in (value as object)) {
    const inner = (value as { value: unknown }).value;
    return typeof inner === "string" ? inner : JSON.stringify(inner, null, 2);
  }
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

interface DiffRow {
  itemId: string;
  a: EvalResult;
  b: EvalResult;
  changed: string[];
}

/**
 * Only rows where a score actually moved. A diff of everything is a wall the
 * reader has to search; a diff of what changed is the answer to "what did this
 * do".
 */
function buildDiff(a: EvalResult[], b: EvalResult[]): DiffRow[] {
  const byItemB = new Map(b.map((r) => [r.item_id, r]));
  const rows: DiffRow[] = [];
  for (const ra of a) {
    const rb = byItemB.get(ra.item_id);
    if (!rb) continue;
    const changed: string[] = [];
    for (const [name, entry] of Object.entries(ra.scores ?? {})) {
      const other = rb.scores?.[name];
      if (!entry || !other) continue;
      if (entry.score !== undefined && other.score !== undefined && entry.score !== other.score) {
        changed.push(name);
      }
    }
    if (changed.length > 0) rows.push({ itemId: ra.item_id, a: ra, b: rb, changed });
  }
  return rows;
}

function ItemDiff({ rows, nameA, nameB }: { rows: DiffRow[]; nameA: string; nameB: string }) {
  if (rows.length === 0) {
    return (
      <p className="px-4 py-6 text-[13px] text-ink-muted">
        No item changed its score between these runs.
      </p>
    );
  }
  return (
    <div className="divide-y divide-line/50">
      {rows.map((row) => (
        <div key={row.itemId} className="px-4 py-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {row.changed.map((name) => {
              const before = row.a.scores?.[name]?.score ?? 0;
              const after = row.b.scores?.[name]?.score ?? 0;
              return (
                <span key={name} className="font-mono text-2xs text-ink-muted">
                  {name}{" "}
                  <span className={after > before ? "text-status-ok" : "text-status-error"}>
                    {before.toFixed(2)} → {after.toFixed(2)}
                  </span>
                </span>
              );
            })}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {[
              { label: nameA, result: row.a },
              { label: nameB, result: row.b },
            ].map(({ label, result }) => (
              <div key={label} className="min-w-0">
                <p className="mb-1 truncate font-mono text-2xs uppercase tracking-wider text-ink-faint">
                  {label}
                </p>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-iron-900 p-2 font-mono text-2xs leading-relaxed text-ink">
                  {asText(result.output) || <span className="text-ink-faint">empty</span>}
                </pre>
                {Object.entries(result.judge_reasoning ?? {}).map(([grader, text]) => (
                  <p key={grader} className="mt-1 text-2xs leading-relaxed text-ink-muted">
                    <span className="font-mono text-ink-faint">{grader}:</span> {text}
                  </p>
                ))}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function CompareView({ projectId }: { projectId: string }) {
  const search = useSearchParams();
  const a = search.get("a");
  const b = search.get("b");

  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [runs, setRuns] = useState<{ a: EvalRun; b: EvalRun } | null>(null);
  const [diff, setDiff] = useState<DiffRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!a || !b) return;
    setError(null);
    Promise.all([compareEvalRuns(a, b), getEvalRun(a), getEvalRun(b), getEvalResults(a), getEvalResults(b)])
      .then(([cmp, runA, runB, resA, resB]) => {
        setComparison(cmp);
        setRuns({ a: runA, b: runB });
        setDiff(buildDiff(resA.items, resB.items));
      })
      .catch((e) =>
        setError(
          e?.message ??
            "Couldn't load the comparison. Both runs must exist and score the same dataset.",
        ),
      );
  };

  useEffect(load, [a, b]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!a || !b) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <p className="max-w-md text-center text-[13px] leading-relaxed text-ink-muted">
          Pick two runs to compare from the{" "}
          <Link href={`/p/${projectId}/evals`} className="text-ember hover:underline">
            eval runs list
          </Link>
          .
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 pt-3">
        <ErrorNote message={error} onRetry={load} />
      </div>
    );
  }

  if (!comparison || !runs || diff === null) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const nameA = runs.a.name || "Run A";
  const nameB = runs.b.name || "Run B";
  const called = comparison.graders.filter((g) => g.verdict !== "inconclusive");

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="border-b border-line px-4 py-4">
        <div className="mb-3 flex flex-wrap items-center gap-2 font-mono text-2xs text-ink-muted">
          <Link href={`/p/${projectId}/evals/${runs.a.id}`} className="hover:text-ink hover:underline">
            {nameA}
          </Link>
          <span aria-hidden>→</span>
          <Link href={`/p/${projectId}/evals/${runs.b.id}`} className="hover:text-ink hover:underline">
            {nameB}
          </Link>
        </div>

        {/* The verdict banner. One sentence per grader, in words. */}
        <div className="space-y-1.5">
          {comparison.graders.map((g) => (
            <p
              key={g.grader}
              className={`text-[13px] leading-relaxed ${
                g.verdict === "inconclusive" ? "text-ink-muted" : "font-medium text-ink"
              }`}
            >
              {verdictSentence(g, nameA, nameB)}
            </p>
          ))}
          {comparison.graders.length === 0 && (
            <p className="text-[13px] text-ink-muted">These runs share no graders to compare.</p>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-2xs text-ink-muted">
          <span>
            compared on {comparison.compared_items} of{" "}
            {Math.max(comparison.run_a.total_items, comparison.run_b.total_items)} items
          </span>
          {comparison.partial && (
            // The dataset changed between the runs, so this is a comparison of
            // the overlap. Saying so is the difference between a partial result
            // and a wrong one.
            <span className="text-status-warn">
              the dataset changed between these runs — only shared items are comparable
            </span>
          )}
          {called.length === 0 && comparison.graders.length > 0 && (
            <span>nothing here reaches significance</span>
          )}
        </div>
      </div>

      <section className="border-b border-line">
        <h2 className="px-4 pt-3 font-mono text-2xs uppercase tracking-wider text-ink-faint">
          Per grader
        </h2>
        <div className="mt-2">
          <GraderTable comparison={comparison} />
        </div>
      </section>

      <section className="grid gap-3 border-b border-line px-4 py-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-iron-900 p-3">
          <p className="font-mono text-2xs uppercase tracking-wider text-ink-faint">Latency</p>
          <p className="mt-1 font-mono text-sm tabular-nums text-ink">
            {formatDuration(comparison.latency.a)} → {formatDuration(comparison.latency.b)}
          </p>
          <p className="mt-0.5 font-mono text-2xs text-ink-muted">
            p95 {formatDuration(comparison.latency.p95_a)} → {formatDuration(comparison.latency.p95_b)}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-iron-900 p-3">
          <p className="font-mono text-2xs uppercase tracking-wider text-ink-faint">Cost</p>
          <p className="mt-1 font-mono text-sm tabular-nums text-ink">
            {formatCost(comparison.cost.a)} → {formatCost(comparison.cost.b)}
          </p>
          <p className="mt-0.5 font-mono text-2xs text-ink-muted">
            {comparison.cost.delta === null
              ? "—"
              : `${comparison.cost.delta >= 0 ? "+" : ""}${formatCost(Math.abs(comparison.cost.delta))} over the compared items`}
          </p>
        </div>
      </section>

      <section>
        <h2 className="px-4 pt-3 font-mono text-2xs uppercase tracking-wider text-ink-faint">
          Items that changed{" "}
          <span className="text-ink-muted">({diff.length})</span>
        </h2>
        <div className="mt-1">
          <ItemDiff rows={diff} nameA={nameA} nameB={nameB} />
        </div>
      </section>
    </div>
  );
}

export default function ComparePage({ params }: { params: { projectId: string } }) {
  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <nav aria-label="Breadcrumb" className="mb-1 font-mono text-2xs text-ink-faint">
          <Link href={`/p/${params.projectId}/evals`} className="hover:text-ink">
            Evals
          </Link>
          <span className="mx-1">/</span>
        </nav>
        <h1 className="font-display text-base font-semibold tracking-tight">Compare runs</h1>
      </header>
      {/* useSearchParams needs a Suspense boundary in the App Router. */}
      <Suspense
        fallback={
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        }
      >
        <CompareView projectId={params.projectId} />
      </Suspense>
    </div>
  );
}
