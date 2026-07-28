"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, createDataset, deleteDataset, listDatasets } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Dataset } from "@/lib/types";
import { ErrorNote, Spinner } from "@/components/ui";

export default function DatasetsPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    setError(null);
    listDatasets(projectId)
      .then(setDatasets)
      .catch(() => setError("Couldn't load datasets. Check that the API is running."));
  };

  useEffect(load, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await createDataset(projectId, name.trim());
      setName("");
      load();
    } catch {
      setError("Couldn't create the dataset. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(dataset: Dataset) {
    try {
      await deleteDataset(dataset.id);
      load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? `"${dataset.name}" has eval runs — delete those first.`
          : "Couldn't delete the dataset. Try again.",
      );
    }
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Datasets</h1>
          {datasets && <span className="font-mono text-2xs text-ink-faint">{datasets.length}</span>}
        </div>
        <form onSubmit={onCreate} className="flex items-center gap-2">
          <label htmlFor="new-dataset" className="sr-only">
            New dataset name
          </label>
          <input
            id="new-dataset"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New dataset name"
            className="field w-48"
          />
          <button type="submit" disabled={busy || !name.trim()} className="btn-ember">
            {busy ? <Spinner className="text-iron-950" /> : "Create"}
          </button>
        </form>
      </header>

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      {datasets === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : datasets.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="max-w-md text-center">
            <h2 className="font-display text-base font-semibold">No datasets yet</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
              A dataset is the ground truth your evals score against. Create one
              here, then promote real traces into it from the Traces page — or
              add items by hand.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <div className="grid grid-cols-[minmax(200px,1fr)_90px_160px_70px] items-center gap-x-3 border-b border-line px-4 py-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
            <span>Name</span>
            <span className="text-right">Items</span>
            <span className="text-right">Created</span>
            <span />
          </div>
          {datasets.map((d) => (
            <div
              key={d.id}
              className="grid grid-cols-[minmax(200px,1fr)_90px_160px_70px] items-center gap-x-3 border-b border-line/50 px-4 py-2.5 transition-colors duration-150 hover:bg-iron-850"
            >
              <Link
                href={`/p/${projectId}/datasets/${d.id}`}
                className="min-w-0 truncate text-[13px] text-ink hover:underline"
              >
                {d.name}
              </Link>
              <span className="text-right font-mono text-2xs text-ink">{d.item_count}</span>
              <span className="text-right font-mono text-2xs text-ink-muted">
                {formatDateTime(d.created_at)}
              </span>
              <button
                type="button"
                onClick={() => onDelete(d)}
                className="cursor-pointer text-right font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-status-error"
              >
                delete
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
