export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-4xl font-semibold tracking-tight">Anvil</h1>
      <p className="max-w-md text-center text-sm text-zinc-500">
        LLM observability &amp; evaluation. Phase 0 skeleton — the dashboard
        (auth, traces, waterfall) lands in Phase 4. See <code>plan/dashboard.md</code>.
      </p>
    </main>
  );
}
