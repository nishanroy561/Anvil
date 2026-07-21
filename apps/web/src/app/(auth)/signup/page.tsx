"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, signup } from "@/lib/api";
import { Spinner } from "@/components/ui";

export default function SignupPage() {
  const router = useRouter();
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signup(email, password, orgName);
      router.replace("/home");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError("Couldn't create the account. Check the details and try again.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Password needs at least 8 characters.");
      } else {
        setError("Couldn't reach the API. Check that it's running, then try again.");
      }
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-lg border border-line bg-iron-900 p-5 shadow-panel">
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Organization
          </span>
          <input
            required
            autoFocus
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            className="field"
            placeholder="Acme Robotics"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Email
          </span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="field"
            placeholder="you@company.dev"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-muted">
            Password
          </span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field"
          />
          <span className="text-2xs text-ink-faint">At least 8 characters</span>
        </label>

        {error && <p className="text-[13px] text-status-error">{error}</p>}

        <button type="submit" disabled={busy} className="btn-ember w-full">
          {busy ? <Spinner className="text-iron-950" /> : "Create account"}
        </button>
      </div>

      <p className="mt-4 text-center text-[13px] text-ink-muted">
        Already have one?{" "}
        <Link href="/login" className="text-ink underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
