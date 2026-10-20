/*
 * Forwards every /api/* request to the MarketLens backend at request time (D-98).
 *
 * On Vercel the backend is an internal service: its address reaches this app only at runtime,
 * through the service binding that sets BACKEND_URL, so the build-time rewrite used before can't
 * reach it. Locally (and with any other backend host) BACKEND_URL is an ordinary environment
 * variable, defaulting to http://127.0.0.1:8000.
 *
 * proxy.ts runs first: it asks for the site password and can add the backend shared secret.
 * The backend's session cookie passes back to the browser unchanged, so it stays first-party.
 */

// Not forwarded: they describe one connection, or are recomputed by fetch.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  "accept-encoding",
  "content-encoding",
]);

function unavailable(message: string): Response {
  return Response.json(
    {
      error: {
        code: "BACKEND_UNAVAILABLE",
        message,
        retryable: true,
        retry_after_seconds: null,
        details: {},
        request_id: null,
      },
    },
    { status: 502, headers: { "cache-control": "no-store" } },
  );
}

async function forward(request: Request): Promise<Response> {
  const base = process.env.BACKEND_URL ?? (process.env.VERCEL ? "" : "http://127.0.0.1:8000");
  if (!base) {
    console.error("BACKEND_URL is not set: add the backend service binding in vercel.json (see docs/DEPLOY_VERCEL.md).");
    return unavailable("MarketLens isn't connected to its backend yet. The site owner needs to finish the setup.");
  }
  const url = new URL(request.url);
  const target = `${base.replace(/\/$/, "")}${url.pathname}${url.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key)) headers.set(key, value);
  });
  const init: RequestInit = { method: request.method, headers, redirect: "manual", cache: "no-store" };
  if (request.method !== "GET" && request.method !== "HEAD") init.body = await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (error) {
    console.error("MarketLens backend request failed:", error instanceof Error ? error.message : error);
    return unavailable("The MarketLens backend is not reachable. Try again in a moment.");
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key) && key !== "set-cookie") responseHeaders.set(key, value);
  });
  for (const cookie of upstream.headers.getSetCookie()) responseHeaders.append("set-cookie", cookie);
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
}

export const GET = forward;
export const HEAD = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
