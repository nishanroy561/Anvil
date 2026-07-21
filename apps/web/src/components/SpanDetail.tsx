"use client";

import { formatCost, formatDuration, formatTokens, shortId } from "@/lib/format";
import type { Span } from "@/lib/types";
import { JsonView } from "./JsonView";
import { StatusBadge } from "./StatusBadge";
import { CopyButton } from "./ui";
import { SPAN_COLORS } from "./spanColors";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-line px-4 py-3 last:border-b-0">
      <h3 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">{title}</h3>
      {children}
    </section>
  );
}

/** `{variable}` placeholders lit up inside a prompt template string. */
function PromptTemplate({ template }: { template: string }) {
  const parts = template.split(/(\{[a-zA-Z0-9_]+\})/g);
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink">
      {parts.map((part, i) =>
        /^\{[a-zA-Z0-9_]+\}$/.test(part) ? (
          <mark key={i} className="rounded bg-ember/20 px-0.5 text-ember">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </pre>
  );
}

function isChunk(v: unknown): v is { text: string; metadata?: Record<string, unknown> } {
  return typeof v === "object" && v !== null && typeof (v as Record<string, unknown>).text === "string";
}

/** Retrieval output as a readable chunk list instead of raw JSON. */
function Chunks({ output }: { output: unknown }) {
  if (!Array.isArray(output) || !output.every(isChunk)) return <JsonView value={output} />;
  return (
    <ol className="flex flex-col gap-2">
      {output.map((chunk, i) => (
        <li key={i} className="rounded-md border border-line bg-iron-850 p-2.5">
          <div className="mb-1.5 flex items-center gap-2 font-mono text-2xs text-ink-faint">
            <span className="text-span-retrieval">#{i + 1}</span>
            {chunk.metadata &&
              Object.entries(chunk.metadata).map(([k, v]) => (
                <span key={k}>
                  {k}={String(v)}
                </span>
              ))}
          </div>
          <p className="whitespace-pre-wrap text-2xs leading-relaxed text-ink">{chunk.text}</p>
        </li>
      ))}
    </ol>
  );
}

export function SpanDetail({ span }: { span: Span }) {
  const attrs = span.attributes ?? {};
  const template = typeof attrs.template === "string" ? attrs.template : null;
  const restAttrs = Object.fromEntries(Object.entries(attrs).filter(([k]) => k !== "template"));

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* identity */}
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-sm"
            style={{ background: SPAN_COLORS[span.type] ?? SPAN_COLORS.custom }}
            aria-hidden="true"
          />
          <h2 className="min-w-0 flex-1 truncate font-display text-sm font-semibold">{span.name}</h2>
          <StatusBadge status={span.status} />
        </div>
        <div className="mt-1.5 flex items-center gap-2 font-mono text-2xs text-ink-faint">
          <span>{span.type}</span>
          <span>·</span>
          <span title={span.id}>{shortId(span.id)}</span>
          <CopyButton value={span.id} label="Copy span id" />
        </div>
      </div>

      {/* the numbers */}
      <Section title="Timing & usage">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-2xs">
          <div className="flex justify-between">
            <dt className="text-ink-muted">duration</dt>
            <dd className="text-ink">
              {span.ended_at
                ? formatDuration(Date.parse(span.ended_at) - Date.parse(span.started_at))
                : "still running…"}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-ink-muted">cost</dt>
            <dd className="text-ink">{formatCost(span.cost_usd)}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-ink-muted">tokens in</dt>
            <dd className="text-ink">{formatTokens(span.tokens_in)}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-ink-muted">tokens out</dt>
            <dd className="text-ink">{formatTokens(span.tokens_out)}</dd>
          </div>
        </dl>
      </Section>

      {span.error && (
        <Section title="Error">
          <pre className="whitespace-pre-wrap break-words rounded-md border border-status-error/30 bg-status-error/5 p-2.5 font-mono text-2xs leading-relaxed text-status-error">
            {span.error}
          </pre>
        </Section>
      )}

      {template && (
        <Section title="Prompt template">
          <PromptTemplate template={template} />
        </Section>
      )}

      <Section title="Input">
        <JsonView value={span.input} />
      </Section>

      <Section title={span.type === "retrieval" ? "Retrieved chunks" : "Output"}>
        {span.type === "retrieval" ? <Chunks output={span.output} /> : <JsonView value={span.output} />}
      </Section>

      {Object.keys(restAttrs).length > 0 && (
        <Section title="Attributes">
          <JsonView value={restAttrs} />
        </Section>
      )}
    </div>
  );
}
