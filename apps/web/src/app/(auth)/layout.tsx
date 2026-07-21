import { AnvilMark } from "@/components/Brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      {/* the forge glow: one ember gradient low on the page, nothing else */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[45vh]"
        style={{
          background:
            "radial-gradient(60% 80% at 50% 100%, rgba(244,118,43,0.14) 0%, rgba(244,118,43,0.04) 45%, transparent 75%)",
        }}
      />
      <div className="relative w-full max-w-[360px]">
        <div className="mb-8 flex flex-col items-center gap-3">
          <AnvilMark className="h-9 w-9 text-ink" />
          <div className="text-center">
            <h1 className="font-display text-xl font-semibold tracking-tight">Anvil</h1>
            <p className="mt-1 text-[13px] text-ink-muted">
              Observability for the LLM features you ship
            </p>
          </div>
        </div>
        {children}
      </div>
    </main>
  );
}
