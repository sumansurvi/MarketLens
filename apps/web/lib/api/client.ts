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

/**
 * Same-origin JSON call to the backend (proxied by Next.js). Errors always surface as
 * ApiError with the server's stable code and safe message.
 */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(path, {
      ...rest,
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
    throw new ApiError("BACKEND_UNAVAILABLE", backendDown(0), 0, {}, null, true);
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
