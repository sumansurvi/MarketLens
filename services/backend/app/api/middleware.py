"""ASGI middleware: request IDs, server-owned session cookie, body-size limit, headers.

The session cookie is an unguessable server-generated token, HTTP-only and SameSite=Lax;
browser JavaScript cannot read it. It binds pending write actions to the session that
created them (MCP_TOOLS §6). Only its hash is stored. Locally the cookie is not ``Secure``
because the app is served over http://localhost; with APP_ENV=production (served over https)
it is.

Hosted deployments protect /api in one of two ways (the health check and the voice worker's
internal API, which has its own bearer token, are exempt from both):

- PROXY_SHARED_SECRET (D-94, a backend on its own host): every request must carry it in
  ``X-MarketLens-Proxy-Secret``. The web app's server-side proxy adds it after checking the site
  password; browsers never see it.
- SITE_PASSWORD (D-99, one Vercel project with /api routed straight to the backend): every
  request must carry the site password as HTTP Basic credentials. Browsers send the credentials
  they entered for the site's pages with every request to the same site, so visitors sign in once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from http.cookies import SimpleCookie

from marketlens_core.hashing import new_id, new_secret_token
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "ml_session"
# CSRF defense in depth (on top of SameSite=Lax): browsers cannot attach a custom header to a
# cross-site request without a CORS preflight, which the backend only allows for FRONTEND_ORIGIN.
CLIENT_HEADER = "x-marketlens-client"
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PROXY_HEADER = "x-marketlens-proxy-secret"
# Reachable without the proxy secret: the host's health check, and the voice worker's API.
DIRECT_PATH = "/api/health"
DIRECT_PREFIX = "/api/internal/voice/"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        secure_cookie: bool = False,
        proxy_secret: str | None = None,
        site_password: str | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.secure_cookie = secure_cookie
        self.proxy_secret = proxy_secret
        self.site_password = site_password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_id("req")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        session = _session_from_cookie(headers.get("cookie", ""))
        new_session = None
        if session is None:
            session = new_secret_token()
            new_session = session
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["session_id"] = session

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        guarded = path.startswith("/api/") and path != DIRECT_PATH and not path.startswith(DIRECT_PREFIX)
        if guarded and self.proxy_secret:
            # Behind the web app's proxy, which already checked the site password.
            if not hmac.compare_digest(headers.get(PROXY_HEADER, "").encode(), self.proxy_secret.encode()):
                await _reject(
                    send,
                    request_id,
                    403,
                    "REQUEST_NOT_ALLOWED",
                    "This backend only accepts requests through the MarketLens web app.",
                )
                return
        elif guarded and self.site_password and not _password_matches(headers.get("authorization"), self.site_password):
            await _reject(
                send,
                request_id,
                401,
                "REQUEST_NOT_ALLOWED",
                "MarketLens is password protected.",
                extra_headers=[(b"www-authenticate", b'Basic realm="MarketLens", charset="UTF-8"')],
            )
            return
        if method in STATE_CHANGING and path.startswith("/api/") and not headers.get(CLIENT_HEADER):
            await _reject(
                send,
                request_id,
                403,
                "REQUEST_NOT_ALLOWED",
                "State-changing requests must come from the MarketLens web client.",
            )
            return

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_body_bytes:
            await _reject_too_large(send, request_id)
            return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            # A streamed (chunked) body over the limit gets a 413 right away; the app then sees a
            # disconnect, and whatever it tries to send afterwards is dropped.
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    if not rejected:
                        rejected = True
                        await _reject_too_large(send, request_id)
                    return {"type": "http.disconnect"}
            return message

        async def send_with_headers(message: Message) -> None:
            if rejected:
                return
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                raw.append((b"x-content-type-options", b"nosniff"))
                raw.append((b"cache-control", b"no-store"))
                raw.append((b"referrer-policy", b"no-referrer"))
                if new_session:
                    cookie = (
                        f"{SESSION_COOKIE}={new_session}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_MAX_AGE}"
                    )
                    if self.secure_cookie:
                        cookie += "; Secure"
                    raw.append((b"set-cookie", cookie.encode()))
                message["headers"] = raw
            await send(message)

        await self.app(scope, limited_receive, send_with_headers)


def _session_from_cookie(header: str) -> str | None:
    if not header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(header)
    except Exception:  # noqa: BLE001 - malformed cookie header: treat as absent
        return None
    morsel = jar.get(SESSION_COOKIE)
    if morsel and _SESSION_RE.fullmatch(morsel.value):
        return morsel.value
    return None


async def _reject_too_large(send: Send, request_id: str) -> None:
    await _reject(send, request_id, 413, "REQUEST_TOO_LARGE", "The request body is too large.")


def _password_matches(header: str | None, password: str) -> bool:
    """Constant-time check of the password in an HTTP Basic ``Authorization`` header (any user name)."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    supplied = decoded.split(":", 1)[1] if ":" in decoded else ""
    return hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(password.encode()).digest())


async def _reject(
    send: Send,
    request_id: str,
    status: int,
    code: str,
    message: str,
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "retry_after_seconds": None,
                "details": {},
                "request_id": request_id,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-request-id", request_id.encode()),
                *(extra_headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
