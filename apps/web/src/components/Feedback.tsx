"use client";

import clsx from "clsx";
import type { FeedbackCounts, FeedbackEvent } from "@/lib/types";
import { formatAgo, formatDateTime } from "@/lib/format";

/**
 * End-user feedback, shown two ways: a count badge for list rows and headers,
 * and a quoted panel for the trace detail.
 *
 * Both obey the same rule — **never colour alone**. A downvote is amber *and* a
 * downward thumb *and* a number, so the signal survives colour blindness, a
 * greyscale print, and a glance at 11px.
 *
 * Downvotes deliberately do not wear the error red. "The request failed" and
 * "the answer was wrong" are different problems with different fixes, and Anvil
 * spends a lot of effort keeping them apart — a drifted trace still returns
 * `ok`. Amber is the same token the tautology warnings use: *this number is not
 * what it looks like*.
 */

export function ThumbIcon({ down, className }: { down: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={clsx("h-3.5 w-3.5", down && "rotate-180", className)}
      aria-hidden="true"
    >
      {/* One path, flipped for the down case: the two states must be mirror
          images or the pair reads as two unrelated glyphs. */}
      <path d="M5 14V7l3.2-4.6c.2-.3.6-.4.9-.2l.5.3c.4.3.6.8.5 1.3L9.4 7h3.1c.9 0 1.6.8 1.4 1.7l-.9 4c-.1.7-.8 1.3-1.5 1.3H5Z" />
      <path d="M5 7H3.2c-.4 0-.7.3-.7.7v5.6c0 .4.3.7.7.7H5" />
    </svg>
  );
}

/**
 * Compact tally for a table cell or a page header. Renders nothing when there is
 * no feedback — an em dash in every row would make the column look like a broken
 * field rather than an empty one.
 */
export function FeedbackBadge({
  counts,
  className,
}: {
  counts: FeedbackCounts | undefined;
  className?: string;
}) {
  const negative = counts?.negative ?? 0;
  const positive = counts?.positive ?? 0;

  if (negative === 0 && positive === 0) {
    return <span className={clsx("font-mono text-2xs text-ink-faint", className)}>—</span>;
  }

  const label = [
    negative > 0 ? `${negative} thumbs down` : null,
    positive > 0 ? `${positive} thumbs up` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <span
      className={clsx("inline-flex items-center gap-1.5", className)}
      title={label}
      aria-label={label}
    >
      {negative > 0 && (
        <span className="inline-flex items-center gap-0.5 font-mono text-2xs tabular-nums text-status-warn">
          <ThumbIcon down />
          {negative}
        </span>
      )}
      {positive > 0 && (
        <span className="inline-flex items-center gap-0.5 font-mono text-2xs tabular-nums text-status-ok">
          <ThumbIcon down={false} />
          {positive}
        </span>
      )}
    </span>
  );
}

/**
 * One vote, quote first.
 *
 * The comment is the only text in this product a real end user wrote, so it is
 * set in the body face at reading size with the machine facts demoted to mono
 * microcopy beneath it. A truncated cell in a grid would bury the one thing
 * worth reading.
 */
export function FeedbackQuote({ event }: { event: FeedbackEvent }) {
  const down = event.rating < 0;
  return (
    <li className="flex gap-2.5">
      <span
        className={clsx("mt-0.5 shrink-0", down ? "text-status-warn" : "text-status-ok")}
        title={down ? "Thumbs down" : "Thumbs up"}
      >
        <ThumbIcon down={down} />
        <span className="sr-only">{down ? "Thumbs down" : "Thumbs up"}</span>
      </span>
      <div className="min-w-0">
        {event.comment ? (
          <p className="text-[13px] leading-relaxed text-ink">
            <span aria-hidden="true" className="text-ink-faint">
              &ldquo;
            </span>
            {event.comment}
            <span aria-hidden="true" className="text-ink-faint">
              &rdquo;
            </span>
          </p>
        ) : (
          <p className="text-[13px] italic text-ink-muted">
            No comment — {down ? "a downvote" : "an upvote"} on its own
          </p>
        )}
        <p className="mt-0.5 font-mono text-2xs text-ink-faint">
          {event.user_ref ?? "anonymous"} ·{" "}
          <time dateTime={event.created_at} title={formatDateTime(event.created_at)}>
            {formatAgo(event.created_at)}
          </time>
        </p>
      </div>
    </li>
  );
}

/** The trace header's feedback section: what the users actually said. */
export function FeedbackPanel({ events }: { events: FeedbackEvent[] }) {
  if (events.length === 0) return null;
  return (
    <section className="border-t border-line px-4 py-3">
      <h2 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">
        End-user feedback
      </h2>
      <ul className="flex max-w-3xl flex-col gap-2.5">
        {events.map((e) => (
          <FeedbackQuote key={e.id} event={e} />
        ))}
      </ul>
    </section>
  );
}
