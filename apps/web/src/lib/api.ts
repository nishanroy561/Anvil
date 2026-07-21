/**
 * Typed API client. All calls go through the Next.js `/api` rewrite so the
 * session cookie is same-origin (see next.config.mjs). The API's error shape
 * is `{"detail": {"code", "message"}}` — surfaced here as ApiError.
 */

import type {
  ApiKey,
  MetricsOverview,
  Project,
  TraceDetail,
  TraceListPage,
  UserOut,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    credentials: "same-origin",
  });

  if (res.status === 204) return undefined as T;

  if (!res.ok) {
    let code = "unknown";
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      const detail = body?.detail ?? body?.error;
      if (typeof detail === "object" && detail !== null) {
        code = detail.code ?? code;
        message = detail.message ?? message;
      } else if (typeof detail === "string") {
        message = detail;
      }
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(res.status, code, message);
  }

  return res.json();
}

// ---- auth -----------------------------------------------------------------

export const login = (email: string, password: string) =>
  request<UserOut>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const signup = (email: string, password: string, orgName: string) =>
  request<UserOut>("/v1/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, org_name: orgName }),
  });

export const logout = () => request<void>("/v1/auth/logout", { method: "POST" });

export const me = () => request<UserOut>("/v1/auth/me");

// ---- projects -------------------------------------------------------------

export const listProjects = () => request<Project[]>("/v1/projects");

export const createProject = (name: string) =>
  request<Project>("/v1/projects", { method: "POST", body: JSON.stringify({ name }) });

export const listApiKeys = (projectId: string) =>
  request<ApiKey[]>(`/v1/projects/${projectId}/api-keys`);

// ---- traces ---------------------------------------------------------------

export interface TraceFilters {
  q?: string;
  status?: string;
  name?: string;
  model?: string;
  since?: string;
  until?: string;
  metadataKey?: string;
  metadataValue?: string;
  cursor?: string;
  limit?: number;
}

export function traceQueryString(f: TraceFilters): string {
  const params = new URLSearchParams();
  if (f.q) params.set("q", f.q);
  if (f.status) params.set("status", f.status);
  if (f.name) params.set("name", f.name);
  if (f.model) params.set("model", f.model);
  if (f.since) params.set("since", f.since);
  if (f.until) params.set("until", f.until);
  if (f.metadataKey && f.metadataValue) params.set(`metadata.${f.metadataKey}`, f.metadataValue);
  if (f.cursor) params.set("cursor", f.cursor);
  if (f.limit) params.set("limit", String(f.limit));
  return params.toString();
}

export const listTraces = (projectId: string, filters: TraceFilters = {}) => {
  const qs = traceQueryString(filters);
  return request<TraceListPage>(`/v1/projects/${projectId}/traces${qs ? `?${qs}` : ""}`);
};

export const getTrace = (traceId: string) => request<TraceDetail>(`/v1/traces/${traceId}`);

export const metricsOverview = (projectId: string, opts: { since?: string; interval?: "hour" | "day" } = {}) => {
  const params = new URLSearchParams();
  if (opts.since) params.set("since", opts.since);
  if (opts.interval) params.set("interval", opts.interval);
  const qs = params.toString();
  return request<MetricsOverview>(`/v1/projects/${projectId}/metrics/overview${qs ? `?${qs}` : ""}`);
};
