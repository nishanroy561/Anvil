"use client";

import clsx from "clsx";
import { useState } from "react";

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={clsx("h-4 w-4 animate-spin text-ink-muted", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-label="Loading"
      role="status"
    >
      <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path
        className="opacity-90"
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="cursor-pointer rounded px-1 font-mono text-2xs text-ink-faint transition-colors duration-150 hover:text-ink"
      aria-label={label}
      title={label}
      onClick={() => {
        navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}

/** Error strip used across pages — explains what happened, offers retry. */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-status-error/30 bg-status-error/5 px-3 py-2 text-[13px] text-status-error">
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-ghost h-7 shrink-0">
          Retry
        </button>
      )}
    </div>
  );
}
