import { createHash, timingSafeEqual } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

/*
 * Runs on the server before every page and /api request (Next 16 "Proxy", formerly middleware; D-94).
 * Both settings are optional, so local development is unchanged:
 *
 * - SITE_PASSWORD: the browser asks for a password (its built-in sign-in box) before anything
 *   loads, so a public deployment isn't open to everyone. Any user name is accepted. On Vercel,
 *   /api goes straight to the backend, which checks the same password itself (D-99).
 * - PROXY_SHARED_SECRET (a backend on its own host, D-94): added to /api requests on their way to
 *   the backend (app/api/[...path]/route.ts), so the backend answers only this web app. It is added
 *   here on the server; browsers never see it. Must match the backend's value.
 */

const SECRET_HEADER = "x-marketlens-proxy-secret";

function digest(value: string): Buffer {
  return createHash("sha256").update(value).digest();
}

/** Constant-time comparison of the password in an HTTP Basic `Authorization` header. */
function passwordMatches(header: string | null, password: string): boolean {
  if (!header?.startsWith("Basic ")) return false;
  let decoded: string;
  try {
    decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
  } catch {
    return false;
  }
  const supplied = decoded.slice(decoded.indexOf(":") + 1);
  return timingSafeEqual(digest(supplied), digest(password));
}

export function proxy(request: NextRequest) {
  const password = process.env.SITE_PASSWORD?.trim();
  if (password && !passwordMatches(request.headers.get("authorization"), password)) {
    return new NextResponse("MarketLens is password protected.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="MarketLens", charset="UTF-8"', "Cache-Control": "no-store" },
    });
  }

  if (!request.nextUrl.pathname.startsWith("/api/")) return NextResponse.next();

  const headers = new Headers(request.headers);
  // Never trust a secret sent by a browser.
  headers.delete(SECRET_HEADER);
  const secret = process.env.PROXY_SHARED_SECRET?.trim();
  if (secret) {
    // A backend on its own host: the secret vouches for the password check above, so the site
    // password itself isn't passed on. Without a secret the backend checks the password itself.
    headers.delete("authorization");
    headers.set(SECRET_HEADER, secret);
  }
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Everything except build assets, which hold no data.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
