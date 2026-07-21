import Link from "next/link";
import { Wordmark } from "@/components/Brand";

/**
 * Public landing, structured the Umami way (centered hero → product frame →
 * compact feature grid → footer) in the iron & ember system. The "screenshot"
 * is a live-rendered waterfall drawn with the real span palette — the product
 * demonstrating itself. Static output — no client JS beyond Next's runtime.
 */

// One believable request: retrieval fans out, one tool call fails and
// retries, the LLM call dominates. Offsets/widths are % of the trace extent.
const HERO_SPANS: {
  name: string;
  type: "chain" | "retrieval" | "llm" | "tool" | "embedding";
  depth: number;
  left: number;
  width: number;
  label: string;
  error?: boolean;
  hot?: boolean;
}[] = [
  { name: "answer_question", type: "chain", depth: 0, left: 0, width: 100, label: "1.84 s" },
  { name: "embed_query", type: "embedding", depth: 1, left: 1, width: 7, label: "128 ms" },
  { name: "vector_search", type: "retrieval", depth: 1, left: 9, width: 13, label: "241 ms" },
  { name: "rerank", type: "retrieval", depth: 2, left: 17, width: 8, label: "147 ms" },
  { name: "fetch_user_context", type: "tool", depth: 1, left: 9, width: 6, label: "110 ms", error: true },
  { name: "fetch_user_context (retry)", type: "tool", depth: 1, left: 16, width: 5, label: "92 ms" },
  { name: "build_prompt", type: "chain", depth: 1, left: 25, width: 4, label: "71 ms" },
  { name: "gpt-4o", type: "llm", depth: 1, left: 30, width: 68, label: "1.25 s", hot: true },
];

const SPAN_HEX: Record<string, string> = {
  llm: "#A78BFA",
  retrieval: "#38BDF8",
  tool: "#FBBF24",
  embedding: "#34D399",
  chain: "#7D8CA3",
};

const SNIPPET = `from anvil_sdk import Anvil, observe

anvil = Anvil(api_key="ak_...")

@observe(anvil, name="answer_question")
def answer(q: str) -> str:
    docs = retriever.search(q)      # retrieval span
    return llm.invoke(prompt(docs)) # llm span, tokens + cost`;

/** Fixed label column; the timeline always starts at the same x. */
const LABEL_W = 190;

function HeroWaterfall() {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-iron-900 shadow-panel">
      {/* browser chrome — the Umami-style product frame */}
      <div className="flex items-center gap-2 border-b border-line bg-iron-850 px-3.5 py-2.5">
        <span className="flex gap-1.5" aria-hidden="true">
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
        </span>
        <span className="ml-2 flex-1 truncate rounded-md border border-line bg-iron-950 px-2.5 py-1 font-mono text-2xs text-ink-faint">
          anvil.local/traces/8f3a1c92
        </span>
      </div>

      {/* trace header, like the real detail page */}
      <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-3.5 py-2.5">
        <span className="font-display text-[13px] font-semibold">answer_question</span>
        <span className="inline-flex items-center gap-1.5 font-mono text-2xs font-medium text-status-ok">
          <span className="h-1.5 w-1.5 rounded-full bg-status-ok" aria-hidden="true" />
          ok
        </span>
        <span className="ml-auto font-mono text-2xs text-ink-faint">1.84 s · $0.0041 · 1.9k tok</span>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          {/* ruler — offset past the label column so ticks align with bars */}
          <div className="flex border-b border-line" aria-hidden="true">
            <div
              className="shrink-0 border-r border-line px-3 py-1 font-mono text-2xs uppercase tracking-wider text-ink-faint"
              style={{ width: LABEL_W }}
            >
              8 spans
            </div>
            <div className="relative h-6 flex-1">
              {[0, 20, 40, 60, 80, 100].map((pct, i) => (
                <span
                  key={pct}
                  className="absolute top-0 flex h-full flex-col justify-between"
                  style={{ left: `${pct}%` }}
                >
                  <span
                    className={`font-mono text-2xs text-ink-faint ${pct === 100 ? "-translate-x-full pr-1" : "pl-1"}`}
                  >
                    {["0", "370ms", "740ms", "1.1s", "1.5s", "1.8s"][i]}
                  </span>
                  <span className="h-1 w-px bg-line-strong" />
                </span>
              ))}
            </div>
          </div>

          {/* rows: fixed label cell | timeline cell */}
          <div aria-label="Example trace waterfall" role="img" className="py-1">
            {HERO_SPANS.map((s, i) => (
              <div key={i} className="flex h-7 items-center">
                {/* label column — truncates, never bleeds into the timeline */}
                <div
                  className="flex h-full shrink-0 items-center gap-1.5 overflow-hidden border-r border-line pr-2"
                  style={{ width: LABEL_W, paddingLeft: 12 + s.depth * 14 }}
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-sm"
                    style={{ background: s.error ? "#F06A5D" : SPAN_HEX[s.type] }}
                    aria-hidden="true"
                  />
                  <span
                    className={`overflow-hidden text-ellipsis whitespace-nowrap font-mono text-2xs ${s.error ? "text-status-error" : "text-ink-muted"}`}
                    title={s.name}
                  >
                    {s.name}
                  </span>
                </div>

                {/* timeline cell — fixed start x, grid + bar + duration */}
                <div className="relative h-full flex-1">
                  {[20, 40, 60, 80].map((pct) => (
                    <span
                      key={pct}
                      className="absolute inset-y-0 w-px bg-line/40"
                      style={{ left: `${pct}%` }}
                      aria-hidden="true"
                    />
                  ))}
                  <span
                    className={`hero-bar absolute top-1/2 h-3 -translate-y-1/2 rounded-[3px] ${s.hot ? "shadow-ember" : ""}`}
                    style={{
                      left: `${s.left}%`,
                      width: `${s.width}%`,
                      background: s.error ? "#F06A5D" : SPAN_HEX[s.type],
                      outline: s.hot ? "1px solid #F4762B" : undefined,
                      outlineOffset: 1,
                      opacity: s.depth === 0 ? 0.35 : 1,
                      animationDelay: `${0.15 + i * 0.08}s`,
                    }}
                  />
                  <span
                    className={`absolute top-1/2 -translate-y-1/2 whitespace-nowrap font-mono text-2xs ${s.hot ? "font-medium text-ember" : s.error ? "text-status-error" : "text-ink-faint"}`}
                    style={
                      s.left + s.width > 78
                        ? { right: `${Math.max(100 - s.left, 2)}%`, paddingRight: 6 }
                        : { left: `${s.left + s.width}%`, paddingLeft: 6 }
                    }
                  >
                    {s.error ? "error" : s.label}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const FEATURES: { title: string; status?: "coming"; body: string }[] = [
  {
    title: "Waterfall tracing",
    body: "The full execution tree of every AI request — nested spans, latency proportions, and the slowest span glowing so you look there first.",
  },
  {
    title: "Cost per request",
    body: "Token counts and dollar cost on every model call, rolled up per trace. Know which feature burns the budget before the invoice does.",
  },
  {
    title: "Kill-safe SDKs",
    body: "Python and TypeScript. If Anvil goes down, your app doesn't: traces buffer client-side and the backlog drains itself on reconnect.",
  },
  {
    title: "Search & filters",
    body: "Free-text search over prompts and outputs, plus filters on status, model, and your own metadata. Errors are one click away.",
  },
  {
    title: "Self-hosted & MIT",
    body: "docker compose up gives you the whole platform on your own hardware. No caps, no nags, and your prompts never leave your network.",
  },
  {
    title: "Evals & monitors",
    status: "coming",
    body: "Promote real traces into datasets, score them with graders and LLM judges, gate CI on regressions, and catch drift in live traffic.",
  },
];

export default function Landing() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      {/* bar entrance: forged left-to-right; globals.css zeroes it for reduced motion */}
      <style>{`
        .hero-bar { transform-origin: left center; animation: forge-in 0.5s cubic-bezier(0.2, 0.8, 0.3, 1) backwards; }
        @keyframes forge-in { from { transform: scaleX(0); } to { transform: scaleX(1); } }
      `}</style>

      {/* the forge glow, high and warm behind the hero */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[80vh]"
        style={{
          background:
            "radial-gradient(50% 50% at 50% 0%, rgba(244,118,43,0.12) 0%, rgba(244,118,43,0.03) 55%, transparent 80%)",
        }}
      />

      <div className="relative mx-auto max-w-5xl px-5">
        {/* nav */}
        <nav className="flex h-16 items-center justify-between" aria-label="Main">
          <Wordmark />
          <div className="flex items-center gap-2">
            <Link href="/login" className="btn-ghost">
              Sign in
            </Link>
            <Link href="/signup" className="btn-ember">
              Get started
            </Link>
          </div>
        </nav>

        {/* hero — centered, Umami-style */}
        <section className="flex flex-col items-center pb-12 pt-16 text-center lg:pt-24">
          <p className="font-mono text-2xs uppercase tracking-[0.2em] text-ember">
            Open-source LLM observability
          </p>
          <h1 className="mt-4 max-w-3xl font-display text-4xl font-bold leading-[1.06] tracking-tight sm:text-5xl lg:text-6xl">
            Know exactly what your AI feature just did.
          </h1>
          <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-ink-muted">
            Anvil traces every AI request span by span — each retrieval, prompt,
            and model call with the tokens and dollars it burned. Ten lines of
            SDK. Self-hosted. MIT.
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
            <Link href="/signup" className="btn-ember h-10 px-6 text-sm">
              Start tracing
            </Link>
            <Link href="/login" className="btn-ghost h-10 px-6 text-sm">
              Sign in
            </Link>
          </div>
          <p className="mt-4 font-mono text-2xs text-ink-faint">
            docker compose up · no caps, no nags, no data leaving your network
          </p>

          {/* the product frame */}
          <div className="mt-12 w-full max-w-4xl text-left">
            <HeroWaterfall />
          </div>
        </section>

        {/* feature grid — compact, six up */}
        <section className="border-t border-line py-14" aria-label="What Anvil does">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-lg border border-line bg-iron-900 p-5">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-display text-[15px] font-semibold">{f.title}</h3>
                  {f.status && (
                    <span className="rounded border border-line px-1.5 py-0.5 font-mono text-2xs text-ink-faint">
                      {f.status}
                    </span>
                  )}
                </div>
                <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* integration snippet */}
        <section className="grid items-center gap-8 border-t border-line py-14 lg:grid-cols-2">
          <div>
            <h2 className="font-display text-2xl font-bold tracking-tight">
              Instrumentation is a decorator, not a migration.
            </h2>
            <p className="mt-3 max-w-md text-[14px] leading-relaxed text-ink-muted">
              Wrap the function that serves your AI feature. Nested calls — the
              retriever, the prompt build, the model — become spans on their
              own. LangChain apps get a callback handler instead.
            </p>
          </div>
          <div className="overflow-hidden rounded-lg border border-line bg-iron-900">
            <div className="border-b border-line px-3 py-1.5 font-mono text-2xs text-ink-faint">
              app.py
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-2xs leading-relaxed text-ink">
              {SNIPPET}
            </pre>
          </div>
        </section>

        {/* footer */}
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line py-8">
          <Wordmark />
          <p className="font-mono text-2xs text-ink-faint">
            MIT licensed · self-hosted by default · FastAPI + Postgres + Redis + Next.js
          </p>
        </footer>
      </div>
    </main>
  );
}
