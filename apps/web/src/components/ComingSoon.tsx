/** Stub for subsystems that land in later phases — honest about when. */
export function ComingSoon({
  title,
  phase,
  description,
}: {
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-line px-4 py-3">
        <h1 className="font-display text-base font-semibold tracking-tight">{title}</h1>
      </header>
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-sm text-center">
          <p className="font-mono text-2xs uppercase tracking-wider text-ember">{phase}</p>
          <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{description}</p>
        </div>
      </div>
    </div>
  );
}
