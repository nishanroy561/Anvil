"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, listProjects } from "@/lib/api";
import { Spinner } from "@/components/ui";

/** Lands on the first project's traces; middleware already gated the cookie. */
export default function Home() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then((projects) => {
        if (projects.length > 0) {
          router.replace(`/p/${projects[0].id}/traces`);
        } else {
          setError("No projects in this organization yet.");
        }
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        } else {
          setError("Couldn't reach the API. Check that it's running, then reload.");
        }
      });
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      {error ? <p className="text-[13px] text-ink-muted">{error}</p> : <Spinner />}
    </main>
  );
}
