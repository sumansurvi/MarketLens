"""The FastAPI app as a Vercel Function (``vercel_backend.asgi:app``).

Lifespan startup runs per instance: migrations (under a Postgres advisory lock) and the three
MCP servers as child processes. Configuration comes from the project's environment variables.
"""

# Importing this module runs vercel_backend/__init__.py first (import paths and Vercel defaults).
from __future__ import annotations

from app.main import create_app
from marketlens_core.config import load_settings

app = create_app(load_settings())
