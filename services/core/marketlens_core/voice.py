"""Shared voice constants and the worker-to-backend credential.

The voice worker calls the backend's internal voice API with a bearer token derived from
``LIVEKIT_API_SECRET`` by HMAC with a fixed purpose label, so no extra secret has to be
configured and the LiveKit secret itself never crosses the wire. Rotating the LiveKit secret
rotates this token.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from marketlens_core.config import Settings

ROOM_PREFIX = "ml-voice-"
ROOM_NAME_RE = re.compile(r"^ml-voice-[A-Za-z0-9_-]{16,40}$")
_PURPOSE = b"marketlens/voice-worker-internal-api/v1"


def internal_token(settings: Settings) -> str | None:
    """Bearer token for the internal voice API, or None when LiveKit is not configured."""
    if not settings.livekit_configured or settings.livekit_api_secret is None:
        return None
    secret = settings.livekit_api_secret.get_secret_value().encode("utf-8")
    return hmac.new(secret, _PURPOSE, hashlib.sha256).hexdigest()


def token_matches(settings: Settings, supplied: str | None) -> bool:
    expected = internal_token(settings)
    return bool(expected and supplied) and hmac.compare_digest(expected, supplied or "")
