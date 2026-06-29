/**
 * Typed fetch client for the ai-digest FastAPI backend.
 *
 * Base URL resolution (first defined wins):
 *   NEXT_PUBLIC_API_BASE  ->  NEXT_PUBLIC_API_URL  ->  http://localhost:8000
 *
 * All endpoints documented in ../API_CONTRACT.md. Server-rendered pages call
 * these with `cache: "no-store"` so the editorial always reflects the latest
 * digest. Errors throw `ApiError` carrying the HTTP status + parsed detail.
 */

import type {
  AnyDigest,
  DigestKind,
  DigestSummary,
  FeedbackRequest,
  FeedbackResponse,
  Health,
  Story,
  TuneRequest,
  TuneResponse,
} from "./types";

export const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function joinUrl(path: string): string {
  const base = API_BASE.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

async function parseDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return res.statusText || "request failed";
  }
}

interface RequestOpts {
  method?: "GET" | "POST";
  body?: unknown;
  /** Pass-through for Next.js fetch caching. Defaults to no-store. */
  cache?: RequestCache;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { method = "GET", body, cache = "no-store", signal } = opts;

  let res: Response;
  try {
    res = await fetch(joinUrl(path), {
      method,
      cache,
      signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    // Network / DNS / connection refused — surface a clean, typed error.
    const reason = err instanceof Error ? err.message : "network error";
    throw new ApiError(0, reason);
  }

  if (!res.ok) {
    throw new ApiError(res.status, await parseDetail(res));
  }

  // 204 or empty body guard.
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

// --------------------------------------------------------------------------- //
// Endpoints
// --------------------------------------------------------------------------- //

export function getHealth(opts?: { signal?: AbortSignal }): Promise<Health> {
  return request<Health>("/api/health", { signal: opts?.signal });
}

export function listDigests(params?: {
  kind?: DigestKind;
  limit?: number;
}): Promise<DigestSummary[]> {
  const search = new URLSearchParams();
  if (params?.kind) search.set("kind", params.kind);
  if (params?.limit != null) search.set("limit", String(params.limit));
  const qs = search.toString();
  return request<DigestSummary[]>(`/api/digests${qs ? `?${qs}` : ""}`);
}

export function getDigest(id: string): Promise<AnyDigest> {
  return request<AnyDigest>(`/api/digest/${encodeURIComponent(id)}`);
}

export function listStories(date?: string): Promise<Story[]> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return request<Story[]>(`/api/stories${qs}`);
}

/**
 * Mutations go through the SAME-ORIGIN Next route handlers (`/app/api/*`), which
 * forward to the backend server-side and inject the optional API key. This keeps
 * `AIDIGEST_API_KEY` off the browser and avoids cross-origin writes.
 */
async function proxyPost<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    const reason = err instanceof Error ? err.message : "network error";
    throw new ApiError(0, reason);
  }
  if (!res.ok) {
    throw new ApiError(res.status, await parseDetail(res));
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export function postFeedback(body: FeedbackRequest): Promise<FeedbackResponse> {
  return proxyPost<FeedbackResponse>("/api/feedback", body);
}

export function postTune(body: TuneRequest): Promise<TuneResponse> {
  return proxyPost<TuneResponse>("/api/tune", body);
}

// --------------------------------------------------------------------------- //
// Server-page helpers that tolerate a down/empty backend gracefully.
// --------------------------------------------------------------------------- //

/** Latest daily digest summary (newest first), or null if none / backend down. */
export async function getLatestDailySummary(): Promise<DigestSummary | null> {
  try {
    const rows = await listDigests({ kind: "daily", limit: 1 });
    return rows[0] ?? null;
  } catch {
    return null;
  }
}

/** Latest weekly digest summary, or null if none / backend down. */
export async function getLatestWeeklySummary(): Promise<DigestSummary | null> {
  try {
    const rows = await listDigests({ kind: "weekly", limit: 1 });
    return rows[0] ?? null;
  } catch {
    return null;
  }
}
