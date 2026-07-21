"use client";

import { useEffect, useState } from "react";
import { listApiKeys, listProjects } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ApiKey, Project } from "@/lib/types";
import { CopyButton, ErrorNote, Spinner } from "@/components/ui";

async function createKey(projectId: string, name: string) {
  const res = await fetch(`/api/v1/projects/${projectId}/api-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("create failed");
  return res.json() as Promise<ApiKey & { key: string }>;
}

async function revokeKey(keyId: string) {
  const res = await fetch(`/api/v1/api-keys/${keyId}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok && res.status !== 204) throw new Error("revoke failed");
}

export default function SettingsPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [project, setProject] = useState<Project | null>(null);
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newKeyName, setNewKeyName] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setError(null);
    Promise.all([listProjects(), listApiKeys(projectId)])
      .then(([projects, apiKeys]) => {
        setProject(projects.find((p) => p.id === projectId) ?? null);
        setKeys(apiKeys);
      })
      .catch(() => setError("Couldn't load project settings. Check that the API is running."));
  };

  useEffect(load, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const created = await createKey(projectId, newKeyName.trim() || "default");
      setFreshKey(created.key);
      setNewKeyName("");
      setKeys(await listApiKeys(projectId));
    } catch {
      setError("Couldn't create the key. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke(id: string) {
    try {
      await revokeKey(id);
      setKeys(await listApiKeys(projectId));
    } catch {
      setError("Couldn't revoke the key. Try again.");
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="border-b border-line px-4 py-3">
        <h1 className="font-display text-base font-semibold tracking-tight">Settings</h1>
      </header>

      <div className="flex max-w-2xl flex-col gap-6 p-4">
        {error && <ErrorNote message={error} onRetry={load} />}

        {/* project identity */}
        <section>
          <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">Project</h2>
          <div className="rounded-lg border border-line bg-iron-900 p-4">
            {project ? (
              <dl className="grid grid-cols-[80px_1fr] gap-y-1.5 text-[13px]">
                <dt className="text-ink-muted">Name</dt>
                <dd className="text-ink">{project.name}</dd>
                <dt className="text-ink-muted">Slug</dt>
                <dd className="font-mono text-ink">{project.slug}</dd>
                <dt className="text-ink-muted">ID</dt>
                <dd className="flex items-center gap-1 font-mono text-ink-muted">
                  {project.id}
                  <CopyButton value={project.id} label="Copy project id" />
                </dd>
              </dl>
            ) : (
              <Spinner />
            )}
          </div>
        </section>

        {/* api keys */}
        <section>
          <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">API keys</h2>

          {freshKey && (
            <div className="mb-3 rounded-lg border border-ember/40 bg-ember/5 p-3">
              <p className="text-[13px] text-ink">
                Key created. Copy it now — it won&apos;t be shown again.
              </p>
              <div className="mt-2 flex items-center gap-2 rounded-md border border-line bg-iron-950 px-2.5 py-1.5">
                <code className="min-w-0 flex-1 truncate font-mono text-2xs text-ember">{freshKey}</code>
                <CopyButton value={freshKey} label="Copy API key" />
              </div>
            </div>
          )}

          <div className="overflow-hidden rounded-lg border border-line bg-iron-900">
            {keys === null ? (
              <div className="flex justify-center p-6">
                <Spinner />
              </div>
            ) : keys.length === 0 ? (
              <p className="p-4 text-[13px] text-ink-muted">No keys yet. Create one below.</p>
            ) : (
              <ul>
                {keys.map((k) => (
                  <li
                    key={k.id}
                    className="flex items-center gap-3 border-b border-line/50 px-4 py-2.5 last:border-b-0"
                  >
                    <code className="font-mono text-2xs text-ink">{k.key_prefix}…</code>
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink-muted">{k.name}</span>
                    <span className="font-mono text-2xs text-ink-faint">
                      {formatDateTime(k.created_at)}
                    </span>
                    {k.revoked_at ? (
                      <span className="font-mono text-2xs text-status-error">revoked</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onRevoke(k.id)}
                        className="cursor-pointer font-mono text-2xs text-ink-muted transition-colors duration-150 hover:text-status-error"
                      >
                        revoke
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <form onSubmit={onCreate} className="mt-3 flex gap-2">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              placeholder="Key name (e.g. production)"
              aria-label="New key name"
              className="field flex-1"
            />
            <button type="submit" disabled={busy} className="btn-ember">
              {busy ? <Spinner className="text-iron-950" /> : "Create key"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
