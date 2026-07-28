"use client";

import { useEffect, useRef, useState } from "react";
import { createDataset, listDatasets, promoteTraces } from "@/lib/api";
import type { Dataset } from "@/lib/types";
import { Spinner } from "@/components/ui";

/**
 * The promote flow (plan/evaluation.md §1): pick a dataset (or create one
 * inline), copy the selected traces in. Promotion is idempotent server-side,
 * so double-clicks and re-promotes are safe.
 */
export function AddToDataset({
  projectId,
  traceIds,
  onDone,
}: {
  projectId: string;
  traceIds: string[];
  onDone?: (result: { promoted: number; skipped: number; dataset: Dataset }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // dataset id being promoted into
  const [feedback, setFeedback] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    listDatasets(projectId).then(setDatasets).catch(() => setDatasets([]));
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, projectId]);

  async function promoteInto(dataset: Dataset) {
    setBusy(dataset.id);
    setFeedback(null);
    try {
      const result = await promoteTraces(dataset.id, traceIds);
      setFeedback(
        result.skipped > 0
          ? `Added ${result.promoted}, skipped ${result.skipped} already present`
          : `Added ${result.promoted} to ${dataset.name}`,
      );
      onDone?.({ ...result, dataset });
      setTimeout(() => {
        setOpen(false);
        setFeedback(null);
      }, 1400);
    } catch {
      setFeedback("Couldn't add traces. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function createAndPromote(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy("new");
    try {
      const dataset = await createDataset(projectId, newName.trim());
      setNewName("");
      await promoteInto(dataset);
    } catch {
      setFeedback("Couldn't create the dataset. Try again.");
      setBusy(null);
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={traceIds.length === 0}
        aria-haspopup="menu"
        aria-expanded={open}
        className="btn-ember disabled:cursor-not-allowed disabled:opacity-50"
      >
        Add to dataset{traceIds.length > 1 ? ` (${traceIds.length})` : ""}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 w-64 rounded-md border border-line bg-iron-850 py-1 shadow-panel"
        >
          {feedback && (
            <p className="border-b border-line px-3 py-2 text-2xs text-ember">{feedback}</p>
          )}

          {datasets === null ? (
            <div className="flex justify-center py-3">
              <Spinner />
            </div>
          ) : (
            <>
              {datasets.length > 0 && (
                <ul className="max-h-48 overflow-y-auto">
                  {datasets.map((d) => (
                    <li key={d.id}>
                      <button
                        type="button"
                        role="menuitem"
                        disabled={busy !== null}
                        onClick={() => promoteInto(d)}
                        className="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-[13px] text-ink transition-colors duration-150 hover:bg-iron-800 disabled:opacity-50"
                      >
                        <span className="min-w-0 flex-1 truncate">{d.name}</span>
                        <span className="font-mono text-2xs text-ink-faint">{d.item_count}</span>
                        {busy === d.id && <Spinner className="h-3 w-3" />}
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <form onSubmit={createAndPromote} className="border-t border-line p-2">
                <label className="mb-1 block text-2xs font-medium uppercase tracking-wider text-ink-faint">
                  New dataset
                </label>
                <div className="flex gap-1.5">
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Name"
                    className="field h-7 min-w-0 flex-1 text-2xs"
                  />
                  <button
                    type="submit"
                    disabled={busy !== null || !newName.trim()}
                    className="btn-ghost h-7 shrink-0 px-2 text-2xs"
                  >
                    {busy === "new" ? <Spinner className="h-3 w-3" /> : "Create"}
                  </button>
                </div>
              </form>
            </>
          )}
        </div>
      )}
    </div>
  );
}
