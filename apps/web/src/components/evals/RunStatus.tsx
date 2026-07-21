import clsx from "clsx";
import type { EvalRunStatus } from "@/lib/types";

const STYLES: Record<EvalRunStatus, { dot: string; text: string; label: string }> = {
  queued: { dot: "bg-ink-faint", text: "text-ink-muted", label: "queued" },
  running: { dot: "bg-status-running animate-pulse-soft", text: "text-status-running", label: "running" },
  completed: { dot: "bg-status-ok", text: "text-status-ok", label: "completed" },
  failed: { dot: "bg-status-error", text: "text-status-error", label: "failed" },
  cancelled: { dot: "bg-status-abandoned", text: "text-status-abandoned", label: "cancelled" },
};

export function RunStatus({ status, className }: { status: EvalRunStatus; className?: string }) {
  const s = STYLES[status] ?? STYLES.queued;
  return (
    <span
      className={clsx("inline-flex items-center gap-1.5 font-mono text-2xs font-medium", s.text, className)}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", s.dot)} aria-hidden="true" />
      {s.label}
    </span>
  );
}

/** Progress toward `total`, ember-filled. Indeterminate while queued. */
export function RunProgress({
  progress,
  total,
  status,
}: {
  progress: number;
  total: number;
  status: EvalRunStatus;
}) {
  const pct = total > 0 ? Math.round((progress / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-iron-800"
        role="progressbar"
        aria-valuenow={progress}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label="Items scored"
      >
        <div
          className={clsx(
            "h-full rounded-full transition-all duration-500",
            status === "failed" ? "bg-status-error" : status === "completed" ? "bg-status-ok" : "bg-ember",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="shrink-0 font-mono text-2xs tabular-nums text-ink-muted">
        {progress}/{total}
      </span>
    </div>
  );
}
