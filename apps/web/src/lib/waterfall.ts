/**
 * Span-tree assembly for the waterfall. Dirty data is normal, not exceptional
 * (plan/dashboard.md §3, checklist row 7): orphans become roots, cycles are
 * broken, open-ended spans get a clock-relative extent.
 */

import type { Span, TraceDetail } from "./types";

export interface WaterfallRow {
  span: Span;
  depth: number;
  children: WaterfallRow[];
  orphan: boolean; // parent id set but unresolvable — rendered at root level
  startMs: number; // offset from trace t0
  endMs: number; // offset from t0; open-ended spans run to the trace edge
  openEnded: boolean;
}

export interface WaterfallModel {
  roots: WaterfallRow[];
  rows: WaterfallRow[]; // depth-first flattening, render order
  totalMs: number; // trace extent, ≥ 1 to keep division safe
  hottestSpanId: string | null; // longest closed span — the ember highlight
}

export function buildWaterfall(trace: TraceDetail): WaterfallModel {
  const spans = trace.spans;
  const byId = new Map(spans.map((s) => [s.id, s]));

  // t0/t1: the trace's own bounds widened by any span that pokes outside them
  // (clock skew survives normalization as clamps, not rejections).
  let t0 = Date.parse(trace.started_at);
  let t1 = trace.ended_at ? Date.parse(trace.ended_at) : Date.now();
  for (const s of spans) {
    const st = Date.parse(s.started_at);
    if (st < t0) t0 = st;
    const en = s.ended_at ? Date.parse(s.ended_at) : null;
    if (en !== null && en > t1) t1 = en;
  }
  const totalMs = Math.max(t1 - t0, 1);

  const nodes = new Map<string, WaterfallRow>();
  for (const s of spans) {
    const startMs = Math.max(Date.parse(s.started_at) - t0, 0);
    const rawEnd = s.ended_at ? Date.parse(s.ended_at) - t0 : totalMs;
    nodes.set(s.id, {
      span: s,
      depth: 0,
      children: [],
      orphan: false,
      startMs,
      // negative-duration data renders as a zero-width tick, never a reversed bar
      endMs: Math.max(rawEnd, startMs),
      openEnded: s.ended_at === null,
    });
  }

  // Parent linkage. Self-parents and dangling ids are orphaned; cycles are
  // broken by refusing any link that would create an ancestor loop.
  const roots: WaterfallRow[] = [];
  for (const node of Array.from(nodes.values())) {
    const pid = node.span.parent_span_id;
    if (pid === null || pid === node.span.id || !byId.has(pid)) {
      node.orphan = pid !== null && pid !== node.span.id && !byId.has(pid);
      roots.push(node);
      continue;
    }
    // walk up from the would-be parent looking for ourselves
    let cursor: string | null = pid;
    let cyclic = false;
    const seen = new Set<string>([node.span.id]);
    while (cursor !== null) {
      if (seen.has(cursor)) {
        cyclic = true;
        break;
      }
      seen.add(cursor);
      cursor = byId.get(cursor)?.parent_span_id ?? null;
    }
    if (cyclic) {
      node.orphan = true;
      roots.push(node);
    } else {
      nodes.get(pid)!.children.push(node);
    }
  }

  // Stable order: by start time, id as tiebreak (matches the API's ordering).
  const byStart = (a: WaterfallRow, b: WaterfallRow) =>
    a.startMs - b.startMs || a.span.id.localeCompare(b.span.id);
  roots.sort(byStart);

  const rows: WaterfallRow[] = [];
  const walk = (node: WaterfallRow, depth: number) => {
    node.depth = depth;
    rows.push(node);
    node.children.sort(byStart);
    for (const c of node.children) walk(c, depth + 1);
  };
  for (const r of roots) walk(r, 0);

  // Hot span: longest *closed* span. Open-ended bars aren't "slow", they're unfinished.
  let hottestSpanId: string | null = null;
  let hottest = -1;
  for (const r of rows) {
    if (r.openEnded) continue;
    const d = r.endMs - r.startMs;
    if (d > hottest) {
      hottest = d;
      hottestSpanId = r.span.id;
    }
  }

  return { roots, rows, totalMs, hottestSpanId };
}

/** Ruler ticks: 5 even divisions, values rounded for reading, not precision. */
export function rulerTicks(totalMs: number): { atPct: number; label: string }[] {
  const ticks = [];
  for (let i = 0; i <= 5; i++) {
    const ms = (totalMs / 5) * i;
    ticks.push({
      atPct: i * 20,
      label: ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)}s` : `${Math.round(ms)}ms`,
    });
  }
  return ticks;
}
