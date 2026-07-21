/**
 * Rendering helpers. The API speaks UTC exclusively (checklist row 14);
 * converting into the viewer's timezone happens here and only here.
 */

/** "421 ms", "1.24 s", "2 m 05 s" — durations stay compact and mono. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m} m ${String(s).padStart(2, "0")} s`;
}

/** Cost in dollars; sub-cent values keep enough precision to be honest. */
export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "—";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(5)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 10_000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** Compact count for metric cards: 1234 → "1.2k". */
export function formatCount(n: number): string {
  return formatTokens(n) ?? "0";
}

/** "14:03:22" local time — trace lists are about *when today*, mostly. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour12: false });
}

/** "Jul 21, 14:03:22" — used when the window can span days. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** "3m ago", "2h ago" — relative, for scanability. */
export function formatAgo(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 0) return "now";
  const s = Math.floor(delta / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/** Short id for display: "a1b2c3d4". Full id lives in the title/copy action. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}
