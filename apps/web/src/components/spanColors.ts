import type { SpanType } from "@/lib/types";

/** Span-type palette — the same hues tailwind.config exposes as span-*. */
export const SPAN_COLORS: Record<SpanType, string> = {
  llm: "#A78BFA",
  retrieval: "#38BDF8",
  tool: "#FBBF24",
  embedding: "#34D399",
  chain: "#7D8CA3",
  custom: "#E879B9",
};
