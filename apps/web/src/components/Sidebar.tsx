"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { listProjects, logout, me } from "@/lib/api";
import type { Project, UserOut } from "@/lib/types";
import { Wordmark } from "./Brand";

/** Nav is the phase map: live sections first, future subsystems greyed in. */
const NAV = [
  { label: "Overview", path: "" },
  { label: "Traces", path: "/traces" },
  // Sits directly under Traces: it is a filtered view of them, and the whole
  // point is that it is the first place to look when users are complaining.
  { label: "Inbox", path: "/inbox" },
  { label: "Datasets", path: "/datasets" },
  { label: "Evals", path: "/evals" },
  { label: "Monitors", path: "/monitors" },
  { label: "Alerts", path: "/alerts" },
  { label: "Settings", path: "/settings" },
];

function NavIcon({ label }: { label: string }) {
  // One 16px stroke icon per section, drawn inline — no icon dependency.
  const paths: Record<string, React.ReactNode> = {
    Overview: <path d="M2 13.5 6.5 8l3 3L14 4.5M10 4h4v4" />,
    Traces: (
      <>
        <path d="M2 4h9M5 8h9M3.5 12h7" />
      </>
    ),
    // A tray with something in it — the "things waiting for you" shape, not a
    // thumb: the thumb is the data, and reusing it here would imply this section
    // is about votes rather than the queue they produce.
    Inbox: (
      <>
        <path d="M2 9.5 3.6 3.2c.1-.4.5-.7.9-.7h7c.4 0 .8.3.9.7L14 9.5v3a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-3Z" />
        <path d="M2 9.5h3l.8 1.6h4.4L11 9.5h3" />
      </>
    ),
    Datasets: (
      <>
        <ellipse cx="8" cy="4" rx="5.5" ry="2.2" />
        <path d="M2.5 4v8c0 1.2 2.5 2.2 5.5 2.2s5.5-1 5.5-2.2V4" />
      </>
    ),
    Evals: <path d="m3 8.5 3.5 3.5L13 4" />,
    Monitors: <path d="M1.5 8h3l2-4.5L10 12l1.8-4H14.5" />,
    Alerts: (
      <>
        <path d="M8 2.5c-2.5 0-3.8 1.8-3.8 4v2.6L3 11h10l-1.2-1.9V6.5c0-2.2-1.3-4-3.8-4Z" />
        <path d="M6.8 13.5a1.3 1.3 0 0 0 2.4 0" />
      </>
    ),
    Settings: (
      <>
        <circle cx="8" cy="8" r="2.2" />
        <path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6 11 5M5 11l-1.4 1.4" />
      </>
    ),
  };
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4 shrink-0"
      aria-hidden="true"
    >
      {paths[label]}
    </svg>
  );
}

function ProjectSwitcher({ projectId, projects }: { projectId: string; projects: Project[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = projects.find((p) => p.id === projectId);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function switchTo(id: string) {
    setOpen(false);
    // Keep the section, swap the project: /p/<old>/traces → /p/<new>/traces
    const section = pathname.replace(/^\/p\/[^/]+/, "");
    router.push(`/p/${id}${section}`);
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-2 rounded-md border border-line bg-iron-850 px-2.5 py-2 text-left transition-colors duration-150 hover:border-line-strong"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-ember/15 font-mono text-2xs font-semibold text-ember">
          {(current?.name ?? "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
          {current?.name ?? "Select project"}
        </span>
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 text-ink-faint" fill="none" aria-hidden="true">
          <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-line bg-iron-850 py-1 shadow-panel"
        >
          {projects.map((p) => (
            <li key={p.id} role="option" aria-selected={p.id === projectId}>
              <button
                type="button"
                onClick={() => switchTo(p.id)}
                className={clsx(
                  "flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-left text-[13px] transition-colors duration-150 hover:bg-iron-800",
                  p.id === projectId ? "text-ink" : "text-ink-muted",
                )}
              >
                <span className="truncate">{p.name}</span>
                {p.id === projectId && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-ember" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Sidebar({ projectId }: { projectId: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [user, setUser] = useState<UserOut | null>(null);

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {});
    me().then(setUser).catch(() => {});
  }, []);

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-iron-900">
      <div className="px-4 pb-4 pt-5">
        <Wordmark />
      </div>

      <div className="px-3 pb-4">
        <ProjectSwitcher projectId={projectId} projects={projects} />
      </div>

      <nav aria-label="Project sections" className="flex-1 px-2">
        <ul className="flex flex-col gap-0.5">
          {NAV.map(({ label, path }) => {
            const href = `/p/${projectId}${path}`;
            const active =
              path === "" ? pathname === `/p/${projectId}` : pathname.startsWith(href);
            return (
              <li key={label}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors duration-150",
                    active
                      ? "bg-iron-800 text-ink"
                      : "text-ink-muted hover:bg-iron-850 hover:text-ink",
                  )}
                >
                  <span className={clsx(active ? "text-ember" : "text-ink-faint")}>
                    <NavIcon label={label} />
                  </span>
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-line px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <span className="min-w-0 truncate font-mono text-2xs text-ink-faint" title={user?.email}>
            {user?.email ?? ""}
          </span>
          <button
            type="button"
            onClick={() => logout().then(() => { router.replace("/login"); router.refresh(); })}
            className="shrink-0 cursor-pointer text-2xs text-ink-muted transition-colors duration-150 hover:text-ink"
          >
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
