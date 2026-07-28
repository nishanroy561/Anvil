/** Mirrors apps/api/anvil_api/schemas — the dashboard's wire types. */

export type TraceStatus = "ok" | "error" | "in_progress" | "abandoned";
export type SpanType = "llm" | "retrieval" | "tool" | "embedding" | "chain" | "custom";

export interface UserOut {
  id: string;
  email: string;
  org_id: string;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface ApiKey {
  id: string;
  key_prefix: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
}

export interface TraceSummary {
  id: string;
  project_id: string;
  name: string;
  status: TraceStatus;
  model: string | null;
  latency_ms: number | null;
  total_tokens_in: number | null;
  total_tokens_out: number | null;
  total_cost_usd: number | null;
  session_id: string | null;
  user_ref: string | null;
  metadata: Record<string, unknown> | null;
  started_at: string;
  ended_at: string | null;
}

export interface TraceListPage {
  items: TraceSummary[];
  next_cursor: string | null;
}

export interface Span {
  id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  type: SpanType;
  status: TraceStatus;
  input: unknown;
  output: unknown;
  attributes: Record<string, unknown> | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  error: string | null;
  started_at: string;
  ended_at: string | null;
}

export interface TraceDetail extends TraceSummary {
  input: unknown;
  output: unknown;
  spans: Span[];
}

export interface TimeBucket {
  bucket: string;
  traces: number;
  errors: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
}

export interface MetricsOverview {
  total_traces: number;
  error_rate: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  interval: "hour" | "day";
  buckets: TimeBucket[];
}

// ---- evaluation (Phase 5) -------------------------------------------------

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  created_at: string;
  item_count: number;
}

export interface DatasetItem {
  id: string;
  dataset_id: string;
  input: unknown;
  expected_output: unknown;
  metadata: Record<string, unknown> | null;
  source_trace_id: string | null;
}

export type EvalRunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface GraderSummary {
  mean: number | null;
  pass_rate: number | null;
  scored_items: number;
  errored_items: number;
  /**
   * Scores where the graded output *was* the reference it was compared against,
   * so a perfect score means nothing (ADR-0018). Counted, not excluded — the
   * score is real, just uninformative. Optional: runs recorded before ADR-0018
   * have no such field.
   */
  tautological_items?: number;
}

export interface EvalRunSummary {
  graders: Record<string, GraderSummary>;
  total_items: number;
  failed_items: number;
  total_cost_usd: number;
  mean_latency_ms: number | null;
  p95_latency_ms: number | null;
}

export interface EvalRun {
  id: string;
  project_id: string;
  dataset_id: string;
  name: string;
  status: EvalRunStatus;
  target_config: { mode: string; model?: string | null; prompt?: string | null } | null;
  grader_config: { graders: { name: string; config: Record<string, unknown> }[] } | null;
  progress: number;
  total: number;
  summary: EvalRunSummary | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface EvalResult {
  id: string;
  run_id: string;
  item_id: string;
  output: unknown;
  scores: Record<
    string,
    { score?: number; detail?: Record<string, unknown>; error?: string; tautological?: boolean }
  > | null;
  judge_reasoning: Record<string, string> | null;
  latency_ms: number | null;
  cost_usd: number | null;
  error: string | null;
}

export interface GraderInfo {
  name: string;
  kind: "deterministic" | "judge";
  description: string;
  config_schema: {
    type: "object";
    properties?: Record<
      string,
      {
        type?: string;
        title?: string;
        description?: string;
        enum?: string[];
        default?: unknown;
        minimum?: number;
        maximum?: number;
        items?: { type?: string };
      }
    >;
    required?: string[];
  };
}

/** Marker shape ingest leaves behind when a field exceeded the size cap. */
export interface TruncationMarker {
  anvil_truncated: true;
  original_bytes?: number;
  preview?: string;
}

export function isTruncationMarker(v: unknown): v is TruncationMarker {
  return typeof v === "object" && v !== null && (v as Record<string, unknown>).anvil_truncated === true;
}
