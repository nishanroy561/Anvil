"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ApiError, createEvalRun, listDatasets, listGraders } from "@/lib/api";
import type { Dataset, GraderInfo } from "@/lib/types";
import { GraderPicker, type GraderSelection } from "@/components/evals/GraderPicker";
import { ErrorNote, Spinner } from "@/components/ui";

function NewRunForm({ projectId }: { projectId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [graders, setGraders] = useState<GraderInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [datasetId, setDatasetId] = useState(searchParams.get("dataset") ?? "");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"rescore" | "generate">("rescore");
  const [model, setModel] = useState("claude-sonnet-5");
  const [prompt, setPrompt] = useState("{{question}}");
  const [selection, setSelection] = useState<GraderSelection>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listDatasets(projectId), listGraders()])
      .then(([d, g]) => {
        setDatasets(d);
        setGraders(g);
        if (!searchParams.get("dataset") && d.length > 0) setDatasetId(d[0].id);
      })
      .catch(() => setLoadError("Couldn't load datasets and graders. Check that the API is running."));
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const dataset = useMemo(
    () => datasets?.find((d) => d.id === datasetId) ?? null,
    [datasets, datasetId],
  );
  const graderCount = Object.keys(selection).length;
  const judgeCount = useMemo(
    () =>
      Object.keys(selection).filter((n) => graders?.find((g) => g.name === n)?.kind === "judge")
        .length,
    [selection, graders],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await createEvalRun({
        dataset_id: datasetId,
        name: name.trim() || `run ${new Date().toISOString().slice(0, 16)}`,
        target: mode === "generate" ? { mode, model: model.trim(), prompt } : { mode },
        graders: Object.entries(selection).map(([n, config]) => ({ name: n, config })),
      });
      router.push(`/p/${projectId}/evals/${run.id}`);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "Couldn't start the run. Check that the API is running.",
      );
      setSubmitting(false);
    }
  }

  if (loadError) {
    return (
      <div className="mx-auto mt-10 max-w-md">
        <ErrorNote message={loadError} />
      </div>
    );
  }

  if (!datasets || !graders) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (datasets.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <p className="text-[13px] leading-relaxed text-ink-muted">
            An eval run needs a dataset to score.{" "}
            <Link href={`/p/${projectId}/datasets`} className="text-ink underline underline-offset-4">
              Create one first
            </Link>
            , then come back here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-4 pb-16">
      {/* 1 — what to score */}
      <section>
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
          1 · Dataset
        </h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-w-56 flex-1 flex-col gap-1">
            <span className="text-2xs font-medium text-ink-muted">Dataset to score</span>
            <select
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="field cursor-pointer"
            >
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.item_count} items)
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-w-56 flex-1 flex-col gap-1">
            <span className="text-2xs font-medium text-ink-muted">Run name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="baseline-v1"
              className="field"
            />
          </label>
        </div>
        {dataset?.item_count === 0 && (
          <p className="mt-2 text-2xs text-status-error">This dataset has no items to score.</p>
        )}
      </section>

      {/* 2 — how to produce the output */}
      <section>
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
          2 · Target
        </h2>
        <div className="flex overflow-hidden rounded-md border border-line" role="group" aria-label="Target mode">
          {(
            [
              ["rescore", "Re-score existing outputs", "Grade what each item already carries — no model calls"],
              ["generate", "Generate fresh outputs", "Run a model + prompt over each item, then grade that"],
            ] as const
          ).map(([value, label, hint]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              aria-pressed={mode === value}
              title={hint}
              className={
                mode === value
                  ? "flex-1 cursor-pointer bg-iron-800 px-3 py-2 text-[13px] text-ink"
                  : "flex-1 cursor-pointer bg-iron-900 px-3 py-2 text-[13px] text-ink-muted transition-colors duration-150 hover:text-ink"
              }
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "generate" && (
          <div className="mt-3 flex flex-col gap-3 rounded-lg border border-line bg-iron-900 p-3">
            <label className="flex flex-col gap-1">
              <span className="text-2xs font-medium text-ink-muted">Model</span>
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required
                className="field font-mono"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-2xs font-medium text-ink-muted">
                Prompt — <code className="text-ember">{"{{variable}}"}</code> fills from each item&apos;s input
              </span>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={4}
                className="rounded-md border border-line bg-iron-950 p-2 font-mono text-2xs leading-relaxed text-ink focus:border-ember focus:outline-none"
              />
            </label>
            <p className="text-2xs text-ink-faint">
              Needs ANTHROPIC_API_KEY configured on the worker. Each item makes one model call.
            </p>
          </div>
        )}
      </section>

      {/* 3 — graders */}
      <section>
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
          3 · Graders
        </h2>
        <GraderPicker graders={graders} selection={selection} onChange={setSelection} />
      </section>

      {/* submit */}
      <div className="sticky bottom-0 -mx-4 border-t border-line bg-iron-950/95 px-4 py-3 backdrop-blur">
        {submitError && <p className="mb-2 text-[13px] text-status-error">{submitError}</p>}
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={submitting || graderCount === 0 || !datasetId || dataset?.item_count === 0}
            className="btn-ember h-9 px-5"
          >
            {submitting ? <Spinner className="text-iron-950" /> : "Start run"}
          </button>
          <span className="font-mono text-2xs text-ink-muted">
            {dataset?.item_count ?? 0} items · {graderCount} graders
            {judgeCount > 0 && (
              <span className="text-ember"> · {judgeCount} judged (costs credits)</span>
            )}
          </span>
        </div>
      </div>
    </form>
  );
}

export default function NewEvalRunPage({ params }: { params: { projectId: string } }) {
  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <nav aria-label="Breadcrumb" className="mb-1 font-mono text-2xs text-ink-faint">
          <Link href={`/p/${params.projectId}/evals`} className="hover:text-ink">
            Evals
          </Link>
          <span className="mx-1">/</span>
        </nav>
        <h1 className="font-display text-base font-semibold tracking-tight">New eval run</h1>
      </header>
      <div className="flex flex-1 flex-col overflow-y-auto">
        <Suspense>
          <NewRunForm projectId={params.projectId} />
        </Suspense>
      </div>
    </div>
  );
}
