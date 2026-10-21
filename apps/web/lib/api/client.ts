import type { ErrorResponse } from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;
  readonly retryable: boolean;

  constructor(
    code: string,
    message: string,
    status: number,
    details: Record<string, unknown> = {},
    requestId: string | null = null,
    retryable = false,
  ) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
    this.requestId = requestId;
    this.retryable = retryable;
  }

  /** Field-level validation issues returned by the API, if any. */
  get fieldIssues(): { field: string; issue: string }[] {
    const fields = this.details["fields"];
    return Array.isArray(fields) ? (fields as { field: string; issue: string }[]) : [];
  }
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** The start command only helps on a developer's machine; a hosted site gets a hint for its operator. */
function backendDown(status: number): string {
  if (typeof window !== "undefined" && LOCAL_HOSTS.has(window.location.hostname)) {
    return "The MarketLens backend is not reachable. Start it with `uv run uvicorn app.main:app --port 8000` and retry.";
  }
  if (status === 0) return "The MarketLens backend is not reachable. Check your connection and retry.";
  return `The MarketLens backend failed to answer (HTTP ${status}). If you run this site, its backend logs say why.`;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const TOOK_TOO_LONG =
  "The request took too long, so it may not have gone through. Check the research history, then try again.";

/**
 * Same-origin JSON call to the backend (proxied by Next.js). Errors always surface as
 * ApiError with the server's stable code and safe message. A call that never answers fails
 * after `timeoutMs` instead of leaving the page waiting (D-102).
 */
export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown; timeoutMs?: number } = {},
): Promise<T> {
  const { json, headers, timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init;
  // Our own timer, not AbortSignal.timeout(), which older browsers lack (and would fail every call).
  const timeout = rest.signal ? null : new AbortController();
  let timedOut = false;
  const timer = timeout
    ? setTimeout(() => {
        timedOut = true;
        timeout.abort();
      }, timeoutMs)
    : undefined;
  let response: Response;
  try {
    response = await fetch(path, {
      ...rest,
      signal: rest.signal ?? timeout?.signal,
      headers: {
        Accept: "application/json",
        // Required by the backend for state-changing requests (CSRF defense in depth).
        "X-MarketLens-Client": "web",
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    if (timedOut) throw new ApiError("REQUEST_TIMEOUT", TOOK_TOO_LONG, 0, {}, null, true);
    throw new ApiError("BACKEND_UNAVAILABLE", backendDown(0), 0, {}, null, true);
  } finally {
    clearTimeout(timer);
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = (body as ErrorResponse | null)?.error;
    if (!error) {
      throw new ApiError("BACKEND_UNAVAILABLE", backendDown(response.status), response.status, {}, null, true);
    }
    throw new ApiError(
      error.code,
      error.message,
      response.status,
      (error.details ?? {}) as Record<string, unknown>,
      error.request_id ?? null,
      Boolean(error.retryable),
    );
  }
  return body as T;
}

/**
 * A run executes inside its execute request (D-103), which lasts as long as the research: the server
 * ends it at the function's 300 s limit, so the page waits a little longer than that.
 */
const EXECUTE_TIMEOUT_MS = 310_000;

/** Runs a queued research in this request. The page follows the run by polling, not by this answer. */
export function executeRun(runId: string): Promise<unknown> {
  return api(`/api/research/${encodeURIComponent(runId)}/execute`, { method: "POST", timeoutMs: EXECUTE_TIMEOUT_MS });
}
