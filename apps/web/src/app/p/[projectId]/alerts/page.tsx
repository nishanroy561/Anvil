"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createAlertRule,
  deleteAlertRule,
  getAlertWebhook,
  listAlertEvents,
  listAlertRules,
  setAlertWebhook,
  updateAlertRule,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AlertComparison, AlertEvent, AlertMetric, AlertRule } from "@/lib/types";
import { ErrorNote, Spinner } from "@/components/ui";

const METRICS: { value: AlertMetric; label: string; unit: string; hint: string }[] = [
  {
    value: "error_rate",
    label: "Error rate",
    unit: "0–1",
    hint: "Fraction of traces in the window that ended in error.",
  },
  {
    value: "cost",
    label: "Cost",
    unit: "USD",
    hint: "Total spend in the window, not an average — a mean hides a volume spike.",
  },
  {
    value: "latency_p95",
    label: "Latency p95",
    unit: "ms",
    hint: "95th percentile trace latency in the window.",
  },
  {
    value: "eval_score",
    label: "Eval score",
    unit: "0–1",
    hint: "Mean score from monitor samples. Needs a monitor to be running.",
  },
];

const COMPARISONS: { value: AlertComparison; label: string }[] = [
  { value: "gt", label: "is above" },
  { value: "gte", label: "is at or above" },
  { value: "lt", label: "is below" },
  { value: "lte", label: "is at or below" },
];

function describe(rule: AlertRule): string {
  const metric = METRICS.find((m) => m.value === rule.metric);
  const comparison = COMPARISONS.find((c) => c.value === rule.comparison);
  return `${metric?.label ?? rule.metric} ${comparison?.label ?? rule.comparison} ${rule.threshold} over ${rule.window_minutes} min`;
}

function formatValue(metric: string | null, value: number): string {
  if (metric === "error_rate" || metric === "eval_score") return value.toFixed(3);
  if (metric === "cost") return `$${value.toFixed(4)}`;
  if (metric === "latency_p95") return `${Math.round(value)} ms`;
  return String(value);
}

function WebhookPanel({ projectId }: { projectId: string }) {
  const [url, setUrl] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAlertWebhook(projectId)
      .then((r) => {
        setUrl(r.alert_webhook_url ?? "");
        setSaved(r.alert_webhook_url);
      })
      .catch(() => setSaved(null));
  }, [projectId]);

  return (
    <section className="rounded-lg border border-line bg-iron-900 p-4">
      <h2 className="font-display text-sm font-semibold tracking-tight">Webhook</h2>
      <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
        Fired and resolved events are POSTed here as JSON. Leave it empty and alerts still
        appear below — the dashboard is the always-on channel.
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const next = await setAlertWebhook(projectId, url.trim() || null);
            setSaved(next.alert_webhook_url);
          } catch (err) {
            setError(err instanceof Error ? err.message : "Couldn't save the webhook.");
          } finally {
            setBusy(false);
          }
        }}
        className="mt-2 flex flex-wrap items-center gap-2"
      >
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://hooks.example.com/anvil"
          aria-label="Alert webhook URL"
          className="min-w-[280px] flex-1 rounded-md border border-line bg-iron-950 px-2 py-1.5 font-mono text-2xs text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
        />
        <button type="submit" disabled={busy} className="btn-ghost">
          {busy ? <Spinner /> : "Save"}
        </button>
        {saved && url.trim() === saved && (
          <span className="font-mono text-2xs text-status-ok">saved</span>
        )}
      </form>
      {error && <p className="mt-1 text-2xs text-status-error">{error}</p>}
    </section>
  );
}

function CreateRuleForm({
  projectId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [metric, setMetric] = useState<AlertMetric>("error_rate");
  const [comparison, setComparison] = useState<AlertComparison>("gt");
  const [threshold, setThreshold] = useState("0.1");
  const [window, setWindow] = useState(15);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = METRICS.find((m) => m.value === metric);

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await createAlertRule(projectId, {
            name,
            metric,
            comparison,
            threshold: Number(threshold),
            window_minutes: window,
          });
          onCreated();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Couldn't create the rule.");
        } finally {
          setBusy(false);
        }
      }}
      className="rounded-lg border border-line bg-iron-900 p-4"
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <label className="flex flex-col gap-1 xl:col-span-2">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">Name</span>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="error rate spike"
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Metric
          </span>
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value as AlertMetric)}
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 text-[13px] text-ink focus:border-ember focus:outline-none"
          >
            {METRICS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Condition
          </span>
          <select
            value={comparison}
            onChange={(e) => setComparison(e.target.value as AlertComparison)}
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 text-[13px] text-ink focus:border-ember focus:outline-none"
          >
            {COMPARISONS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Threshold ({selected?.unit})
          </span>
          <input
            required
            type="number"
            step="any"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            className="rounded-md border border-line bg-iron-950 px-2 py-1.5 font-mono text-[13px] text-ink focus:border-ember focus:outline-none"
          />
        </label>
      </div>

      <label className="mt-3 flex max-w-xs flex-col gap-1">
        <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
          Window — {window} minutes
        </span>
        <input
          type="range"
          min={5}
          max={240}
          step={5}
          value={window}
          onChange={(e) => setWindow(Number(e.target.value))}
          className="mt-1 accent-ember"
        />
      </label>

      <p className="mt-2 text-2xs leading-relaxed text-ink-muted">{selected?.hint}</p>
      <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
        No data is never a breach, so a quiet project doesn&apos;t alert. While a rule is
        breaching it fires once and stays open until it recovers.
      </p>

      {error && <p className="mt-2 text-2xs text-status-error">{error}</p>}

      <div className="mt-3 flex items-center gap-2">
        <button type="submit" disabled={busy} className="btn-ember">
          {busy ? <Spinner className="text-iron-950" /> : "Create rule"}
        </button>
        <button type="button" onClick={onCancel} className="btn-ghost">
          Cancel
        </button>
      </div>
    </form>
  );
}

function EventRow({ event }: { event: AlertEvent }) {
  const active = event.resolved_at === null;
  return (
    <tr className="border-b border-line/50">
      <td className="px-4 py-2">
        <span
          className={`inline-flex items-center gap-1.5 font-mono text-2xs font-medium ${
            active ? "text-status-error" : "text-status-ok"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              active ? "bg-status-error animate-pulse-soft" : "bg-status-ok"
            }`}
            aria-hidden
          />
          {active ? "firing" : "resolved"}
        </span>
      </td>
      <td className="px-3 py-2 text-[13px] text-ink">{event.rule_name ?? "—"}</td>
      <td className="px-3 py-2 text-right font-mono text-2xs tabular-nums text-ink">
        {formatValue(event.metric, event.value)}
      </td>
      <td className="px-3 py-2 font-mono text-2xs text-ink-muted">
        {formatDateTime(event.fired_at)}
      </td>
      <td className="px-3 py-2 font-mono text-2xs text-ink-muted">
        {event.resolved_at ? formatDateTime(event.resolved_at) : "—"}
      </td>
      <td className="px-4 py-2 text-right">
        {!event.notified && (
          // The alert fired; only delivery failed. Worth saying, because a
          // silent webhook looks identical to no alert at all.
          <span
            className="font-mono text-2xs text-status-warn"
            title="The event fired but a channel could not be reached."
          >
            not delivered
          </span>
        )}
      </td>
    </tr>
  );
}

export default function AlertsPage({ params }: { params: { projectId: string } }) {
  const { projectId } = params;
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [events, setEvents] = useState<AlertEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setError(null);
    Promise.all([listAlertRules(projectId), listAlertEvents(projectId)])
      .then(([r, e]) => {
        setRules(r);
        setEvents(e);
      })
      .catch(() => setError("Couldn't load alerts. Check that the API is running."));
  }, [projectId]);

  useEffect(load, [load]);

  const firing = (events ?? []).filter((e) => e.resolved_at === null).length;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-base font-semibold tracking-tight">Alerts</h1>
          {firing > 0 && (
            <span className="font-mono text-2xs font-medium text-status-error">
              {firing} firing
            </span>
          )}
        </div>
        <button type="button" onClick={() => setCreating((v) => !v)} className="btn-ember">
          {creating ? "Close" : "New rule"}
        </button>
      </header>

      {error && (
        <div className="px-4 pt-3">
          <ErrorNote message={error} onRetry={load} />
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {creating && (
          <CreateRuleForm
            projectId={projectId}
            onCreated={() => {
              setCreating(false);
              load();
            }}
            onCancel={() => setCreating(false)}
          />
        )}

        <WebhookPanel projectId={projectId} />

        <section className="rounded-lg border border-line bg-iron-900 p-4">
          <h2 className="font-display text-sm font-semibold tracking-tight">Rules</h2>
          {rules === null ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : rules.length === 0 ? (
            <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
              No rules yet. A rule watches one metric over a trailing window and opens an
              event when it crosses your threshold.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-line/50">
              {rules.map((rule) => (
                <li key={rule.id} className="flex flex-wrap items-center gap-3 py-2">
                  <span className="text-[13px] text-ink">{rule.name}</span>
                  <span className="font-mono text-2xs text-ink-muted">{describe(rule)}</span>
                  {!rule.enabled && (
                    <span className="font-mono text-2xs text-status-warn">disabled</span>
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      type="button"
                      aria-pressed={rule.enabled}
                      onClick={async () => {
                        await updateAlertRule(rule.id, { enabled: !rule.enabled });
                        load();
                      }}
                      className="btn-ghost h-7 px-2 text-2xs"
                    >
                      {rule.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        await deleteAlertRule(rule.id);
                        load();
                      }}
                      className="cursor-pointer font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-status-error"
                    >
                      delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-lg border border-line bg-iron-900">
          <h2 className="px-4 pt-4 font-display text-sm font-semibold tracking-tight">History</h2>
          {events === null ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : events.length === 0 ? (
            <p className="px-4 pb-4 pt-2 text-2xs leading-relaxed text-ink-muted">
              Nothing has fired. Rules are evaluated every 60 seconds.
            </p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse">
                <thead>
                  <tr className="border-b border-line text-left font-mono text-2xs uppercase tracking-wider text-ink-faint">
                    <th className="px-4 py-2 font-medium">State</th>
                    <th className="px-3 py-2 font-medium">Rule</th>
                    <th className="px-3 py-2 text-right font-medium">Value</th>
                    <th className="px-3 py-2 font-medium">Fired</th>
                    <th className="px-3 py-2 font-medium">Resolved</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <EventRow key={e.id} event={e} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
