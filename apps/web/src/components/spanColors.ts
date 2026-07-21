import type { SpanType } from "@/lib/types";

/** Span-type palette — the same hues tailwind.config exposes as span-*. */
export const SPAN_COLORS: Record<SpanType, string> = {
  llm: "#7C3AED",
  retrieval: "#1570EF",
  tool: "#DC6803",
  embedding: "#039855",
  chain: "#64748B",
  custom: "#DD2590",
};
