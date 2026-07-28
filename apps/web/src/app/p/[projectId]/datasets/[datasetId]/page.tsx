"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  addDatasetItems,
  deleteDatasetItem,
  getDataset,
  listDatasetItems,
  updateDatasetItem,
} from "@/lib/api";
import type { Dataset, DatasetItem } from "@/lib/types";
import { ErrorNote, Spinner } from "@/components/ui";

function asEditorText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

/** Text back to a payload: JSON when it parses, plain string otherwise. */
function fromEditorText(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

/**
 * One editable cell. Click to edit, save/cancel with visible feedback —
 * editable expected outputs are the point of this table (plan §1).
 */
function EditableCell({
  value,
  onSave,
  label,
}: {
  value: unknown;
  onSave: (next: unknown) => Promise<void>;
  label: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);
  const text = asEditorText(value);

  // A promoted output is routinely taller than the clamp, and JSON cut
  // mid-object with no affordance reads as corrupt rather than truncated.
  useEffect(() => {
    const el = preRef.current;
    if (!el || expanded) return;
    setClipped(el.scrollHeight > el.clientHeight + 1);
  }, [text, expanded, editing]);

  if (!editing) {
    return (
      <div className="relative">
        <button
          type="button"
          onClick={() => {
            setDraft(text);
            setEditing(true);
          }}
          aria-label={`Edit ${label}`}
          title="Click to edit"
          className="block w-full cursor-pointer rounded px-1 py-0.5 text-left transition-colors duration-150 hover:bg-iron-800"
        >
          <pre
            ref={preRef}
            className={`whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink ${
              expanded ? "" : "max-h-24 overflow-hidden"
            }`}
          >
            {text || <span className="text-ink-faint">empty</span>}
          </pre>
        </button>
        {(clipped || expanded) && (
          <>
            {!expanded && (
              <div
                aria-hidden
                className="pointer-events-none absolute inset-x-0 bottom-4 h-6 bg-gradient-to-b from-transparent to-iron-950"
              />
            )}
            {/* Sibling rather than nested — a button inside the edit button
                would be invalid markup and unreachable by keyboard. */}
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="relative cursor-pointer px-1 font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-ink"
            >
              {expanded ? "show less" : "show more"}
            </button>
          </>
        )}
      </div>
    );
  }

  return (
    <div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        autoFocus
        aria-label={label}
        className="w-full rounded-md border border-ember bg-iron-950 p-2 font-mono text-2xs leading-relaxed text-ink focus:outline-none"
      />
      <div className="mt-1 flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setFailed(false);
            try {
              await onSave(fromEditorText(draft));
              setEditing(false);
            } catch {
              setFailed(true);
            } finally {
              setBusy(false);
            }
          }}
          className="btn-ember h-6 px-2 text-2xs"
        >
          {busy ? <Spinner className="h-3 w-3 text-iron-950" /> : "Save"}
        </button>
        <button type="button" onClick={() => setEditing(false)} className="btn-ghost h-6 px-2 text-2xs">
          Cancel
        </button>
        {failed && <span className="text-2xs text-status-error">Save failed — try again</span>}
      </div>
    </div>
  );
}

export default function DatasetDetailPage({
  params,
}: {
  params: { projectId: string; datasetId: string };
}) {
  const { projectId, datasetId } = params;
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [items, setItems] = useState<DatasetItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newInput, setNewInput] = useState("");
  const [newExpected, setNewExpected] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    setError(null);
    Promise.all([getDataset(datasetId), listDatasetItems(datasetId)])
      .then(([d, page]) => {
        setDataset(d);
        setItems(page.items);
        setTotal(page.total);
      })
      .catch(() => setError("Couldn't load the dataset. Check that the API is running."));
  };

  useEffect(load, [datasetId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onAdd(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await addDatasetItems(datasetId, [
        { input: fromEditorText(newInput), expected_output: fromEditorText(newExpected) },
      ]);
      setNewInput("");
      setNewExpected("");
      setAdding(false);
      load();
    } catch {
      setError("Couldn't add the item. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <nav aria-label="Breadcrumb" className="mb-1 font-mono text-2xs text-ink-faint">
          <Link href={`/p/${projectId}/datasets`} className="hover:text-ink">
            Datasets
          </Link>
          <span className="mx-1">/</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">
            {dataset?.name ?? "…"}
          </h1>
          <span className="font-mono text-2xs text-ink-faint">{total} items</span>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" onClick={() => setAdding((v) => !v)} className="btn-ghost">
              {adding ? "Close" : "Add item"}
            </button>
            <Link
              href={`/p/${projectId}/evals/new?dataset=${datasetId}`}
              className="btn-ember"
              aria-disabled={total === 0}
            >
              Run eval
            </Link>
          </div>
        </div>
      </header>

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      {adding && (
        <form onSubmit={onAdd} className="border-b border-line bg-iron-900 px-4 py-3">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="flex flex-col gap-1">
              <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
                Input (JSON or text)
              </span>
              <textarea
                value={newInput}
                onChange={(e) => setNewInput(e.target.value)}
                rows={3}
                placeholder='{"question": "…"}'
                className="rounded-md border border-line bg-iron-950 p-2 font-mono text-2xs text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
                Expected output
              </span>
              <textarea
                value={newExpected}
                onChange={(e) => setNewExpected(e.target.value)}
                rows={3}
                placeholder="The answer the system should give"
                className="rounded-md border border-line bg-iron-950 p-2 font-mono text-2xs text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
              />
            </label>
          </div>
          <button type="submit" disabled={busy} className="btn-ember mt-2">
            {busy ? <Spinner className="text-iron-950" /> : "Add item"}
          </button>
        </form>
      )}

      {items === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <p className="max-w-md text-center text-[13px] leading-relaxed text-ink-muted">
            No items yet. Add one above, or select traces on the Traces page and
            use “Add to dataset”.
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-auto">
          <table className="w-full min-w-[720px] border-collapse">
            <thead className="sticky top-0 z-10 bg-iron-950">
              <tr className="border-b border-line text-left font-mono text-2xs uppercase tracking-wider text-ink-faint">
                <th className="px-4 py-2 font-medium">Input</th>
                <th className="px-4 py-2 font-medium">Expected output</th>
                <th className="w-24 px-4 py-2 font-medium">Source</th>
                <th className="w-16 px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-line/50 align-top">
                  <td className="w-[40%] px-3 py-2">
                    <EditableCell
                      label="item input"
                      value={item.input}
                      onSave={async (next) => {
                        await updateDatasetItem(item.id, { input: next });
                        load();
                      }}
                    />
                  </td>
                  <td className="w-[40%] px-3 py-2">
                    <EditableCell
                      label="expected output"
                      value={item.expected_output}
                      onSave={async (next) => {
                        await updateDatasetItem(item.id, { expected_output: next });
                        load();
                      }}
                    />
                  </td>
                  <td className="px-4 py-2.5">
                    {item.source_trace_id ? (
                      <Link
                        href={`/traces/${item.source_trace_id}`}
                        className="font-mono text-2xs text-span-retrieval hover:underline"
                        title="Open the source trace"
                      >
                        trace ↗
                      </Link>
                    ) : (
                      <span className="font-mono text-2xs text-ink-faint">manual</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={async () => {
                        await deleteDatasetItem(item.id).catch(() => setError("Couldn't delete the item."));
                        load();
                      }}
                      className="cursor-pointer font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-status-error"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
