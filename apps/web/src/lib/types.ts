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

/** Per-trace thumbs tally. Counts only — comments live on the detail response. */
export interface FeedbackCounts {
  negative: number;
  positive: number;
}

export interface FeedbackEvent {
  id: string;
  trace_id: string;
  /** -1 thumbs-down, +1 thumbs-up. */
  rating: number;
  comment: string | null;
  user_ref: string | null;
  created_at: string;
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
  feedback: FeedbackCounts;
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
  /** The entries behind `feedback`'s counts, newest first, comments included. */
  feedback_events: FeedbackEvent[];
}

/** A downvoted trace nobody has turned into a test case yet (Phase 9). */
export interface FailureInboxItem {
  trace: TraceSummary;
  feedback: FeedbackEvent[];
  latest_feedback_at: string;
}

export interface FailureInboxPage {
  items: FailureInboxItem[];
  total: number;
}

// ---- usage rollups (Phase 10) ---------------------------------------------

/** One org-month. Counters only ever move up (ADR-0022): `traces_ingested` means
 *  "ingested during this month", so retention deleting the traces doesn't reduce
 *  it. There is deliberately no quota or limit field — Anvil has no tiers. */
export interface UsageMonth {
  month: string;
  traces_ingested: number;
  eval_items: number;
  /** Always null: per-org bytes are not honestly attributable (ADR-0022). */
  storage_bytes: number | null;
  updated_at: string | null;
}

export interface UsageSummary {
  months: UsageMonth[];
  /** null means traces are kept forever. */
  retention_days: number | null;
  current_month: UsageMonth | null;
}

export interface TimeBucket {
  bucket: string;
  traces: number;
  errors: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  feedback_negative: number;
  feedback_positive: number;
}

export interface MetricsOverview {
  total_traces: number;
  error_rate: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_feedback_negative: number;
  total_feedback_positive: number;
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

/** Why a grader declined to call a result. `null` when it did call one. */
export type InconclusiveReason =
  | "no_paired_scores"
  | "tautological"
  | "insufficient_data"
  | "ci_contains_zero";

export interface GraderComparison {
  grader: string;
  n: number;
  mean_a: number | null;
  mean_b: number | null;
  delta: number | null;
  ci: [number, number] | null;
  p_value: number | null;
  significant: boolean;
  verdict: "improved" | "regressed" | "inconclusive";
  reason: InconclusiveReason | null;
  tautological_pairs: number;
}

export interface RunComparison {
  run_a: { id: string; name: string | null; total_items: number };
  run_b: { id: string; name: string | null; total_items: number };
  compared_items: number;
  /** The snapshots differed, so this compared fewer items than one run scored. */
  partial: boolean;
  graders: GraderComparison[];
  latency: {
    a: number | null;
    b: number | null;
    delta: number | null;
    p95_a: number | null;
    p95_b: number | null;
    p95_delta: number | null;
  };
  cost: {
    a: number;
    b: number;
    delta: number | null;
    mean_a: number | null;
    mean_b: number | null;
  };
}

export interface GraderInfo {
  name: string;
  kind: "deterministic" | "judge";
  description: string;
  /** "plugin" means it came from a package installed in the worker (ADR-0023) —
   *  worth showing, since a teammate may not recognise the name. */
  source?: "builtin" | "plugin";
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

// ---- monitors & alerts (Phase 8) -------------------------------------------

export interface Monitor {
  id: string;
  project_id: string;
  name: string;
  sample_rate: number;
  schedule_cron: string;
  grader_config: { graders: { name: string; config: Record<string, unknown> }[] } | null;
  enabled: boolean;
  last_run_at: string | null;
  created_at: string;
}

export interface MonitorSample {
  id: string;
  monitor_id: string;
  trace_id: string;
  scores: Record<string, { score?: number; detail?: Record<string, unknown>; error?: string }> | null;
  created_at: string;
}

export interface ScoreBucket {
  bucket: string;
  /** Null when every sample in the bucket errored — not zero. */
  mean_score: number | null;
  samples: number;
}

export interface MonitorScoreSeries {
  buckets: ScoreBucket[];
  graders: string[];
}

export type AlertMetric = "error_rate" | "cost" | "latency_p95" | "eval_score";
export type AlertComparison = "gt" | "lt" | "gte" | "lte";

export interface AlertRule {
  id: string;
  project_id: string;
  name: string;
  metric: AlertMetric;
  comparison: AlertComparison;
  threshold: number;
  window_minutes: number;
  enabled: boolean;
  created_at: string;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name: string | null;
  metric: string | null;
  fired_at: string;
  value: number;
  resolved_at: string | null;
  /** False means it fired but a channel could not be reached. */
  notified: boolean;
}
