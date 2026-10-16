"""ASGI middleware: request IDs, server-owned session cookie, body-size limit, headers.

The session cookie is an unguessable server-generated token, HTTP-only and SameSite=Lax;
browser JavaScript cannot read it. It binds pending write actions to the session that
created them (MCP_TOOLS §6). Only its hash is stored. Locally the cookie is not ``Secure``
because the app is served over http://localhost; with APP_ENV=production (served over https)
it is.

Hosted deployments can set PROXY_SHARED_SECRET (D-94): every /api request except the health
check and the voice worker's internal API (which has its own bearer token) must then carry it
in ``X-MarketLens-Proxy-Secret``. The web app's server-side proxy adds it; browsers never see it.
"""

from __future__ import annotations

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
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.secure_cookie = secure_cookie
        self.proxy_secret = proxy_secret

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
        if (
            self.proxy_secret
            and path.startswith("/api/")
            and path != DIRECT_PATH
            and not path.startswith(DIRECT_PREFIX)
            and not hmac.compare_digest(headers.get(PROXY_HEADER, "").encode(), self.proxy_secret.encode())
        ):
            await _reject(
                send,
                request_id,
                403,
                "REQUEST_NOT_ALLOWED",
                "This backend only accepts requests through the MarketLens web app.",
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


async def _reject(send: Send, request_id: str, status: int, code: str, message: str) -> None:
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
            "headers": [(b"content-type", b"application/json"), (b"x-request-id", request_id.encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
