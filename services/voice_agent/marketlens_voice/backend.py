"""HTTP client for the backend's internal voice API (the worker's only data path).

Every call is scoped to one LiveKit room; the backend maps the room to the browser session
that created it. The worker can start research and read status and summaries, nothing else.
"""

from __future__ import annotations

from typing import Any

import httpx

CLIENT_HEADER = {"X-MarketLens-Client": "voice-worker"}


class BackendError(Exception):
    """A backend error with its stable code and a user-safe message."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class VoiceBackendClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        room_name: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.room_name = room_name
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self._headers = {**CLIENT_HEADER, "Authorization": f"Bearer {token}"}
        self._prefix = f"/api/internal/voice/rooms/{room_name}"

    async def start_research(self, *, symbol: str, question: str, horizon: str | None) -> dict[str, Any]:
        body = {"symbol": symbol, "question": question, "horizon": horizon}
        return await self._request("POST", "/research", json=body)

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        return await self._request("GET", "/status", params={"run_id": run_id} if run_id else None)

    async def summary(self, run_id: str | None = None, focus: str = "overview") -> dict[str, Any]:
        params = {"focus": focus, **({"run_id": run_id} if run_id else {})}
        return await self._request("GET", "/summary", params=params)

    async def end(self) -> None:
        await self._request("POST", "/end")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, self._prefix + path, headers=self._headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise BackendError(
                "BACKEND_TIMEOUT", "The research service did not respond in time.", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(
                "BACKEND_UNAVAILABLE", "The research service is not reachable right now.", retryable=True
            ) from exc
        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendError("BACKEND_ERROR", "The research service returned an unreadable response.") from exc
        if response.is_success:
            return payload
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            raise BackendError("BACKEND_ERROR", "The research service rejected the request.")
        return_code = str(error.get("code") or "BACKEND_ERROR")
        message = str(error.get("message") or "The research service rejected the request.")
        raise BackendError(return_code, message, retryable=bool(error.get("retryable")))
