"""The FastAPI app as a Vercel Function (``vercel_backend.asgi:app``).

Lifespan startup runs per instance: migrations (under a Postgres advisory lock) and the three
MCP servers as child processes. Configuration comes from the project's environment variables.
When it is invalid, the app still starts and answers every /api request with a 503 that names
the setting to fix, instead of crashing into Vercel's generic error page (D-101).
"""

# Importing this module runs vercel_backend/__init__.py first (import paths, Vercel marker).
from __future__ import annotations

from app.main import hosted_app_from_settings

app = hosted_app_from_settings()
