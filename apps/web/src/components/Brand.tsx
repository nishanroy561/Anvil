import clsx from "clsx";

/**
 * The Anvil mark: a forged block with the ember face. Drawn, not imported —
 * it is the one place the brand exists, so it stays an SVG we own.
 */
export function AnvilMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className={clsx("h-5 w-5", className)}
    >
      {/* anvil silhouette */}
      <path
        d="M3 6h18v3h-7v2.5c0 1.5 1.5 2.5 3.5 2.5v2h-11v-2c2 0 3.5-1 3.5-2.5V9H7C4.8 9 3 7.8 3 6.5V6Z"
        fill="currentColor"
      />
      <rect x="6" y="18" width="12" height="2.5" rx="0.5" fill="currentColor" />
      {/* accent face */}
      <rect x="3" y="6" width="18" height="1.2" fill="#3B76FF" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={clsx("flex items-center gap-2", className)}>
      <AnvilMark className="text-ink" />
      <span className="font-display text-[15px] font-semibold tracking-tight text-ink">
        Anvil
      </span>
    </span>
  );
}
