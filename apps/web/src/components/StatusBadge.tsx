import clsx from "clsx";
import type { TraceStatus } from "@/lib/types";

const STYLES: Record<TraceStatus, { dot: string; text: string; label: string }> = {
  ok: { dot: "bg-status-ok", text: "text-status-ok", label: "ok" },
  error: { dot: "bg-status-error", text: "text-status-error", label: "error" },
  in_progress: {
    dot: "bg-status-running animate-pulse-soft",
    text: "text-status-running",
    label: "running",
  },
  abandoned: { dot: "bg-status-abandoned", text: "text-status-abandoned", label: "abandoned" },
};

export function StatusBadge({ status, className }: { status: TraceStatus; className?: string }) {
  const s = STYLES[status] ?? STYLES.abandoned;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 font-mono text-2xs font-medium",
        s.text,
        className,
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", s.dot)} aria-hidden="true" />
      {s.label}
    </span>
  );
}
