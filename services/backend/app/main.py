"""FastAPI application factory.

Run: ``uv run uvicorn app.main:app --host 127.0.0.1 --port 8000``. ``app`` is created
lazily on first access so importing this module (e.g. in tests) has no side effects.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from marketlens_core import __version__
from marketlens_core.config import ConfigError, Settings, load_settings
from marketlens_core.db import initialize_database

from app.api import actions, analytics, compare, health, research, symbols, voice, workspace
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.container import build_container
from app.mcp.gateway import ToolGateway
from app.providers.llm.base import LLMProvider

logger = logging.getLogger("marketlens")


def create_app(settings: Settings, *, gateway: ToolGateway | None = None, llm: LLMProvider | None = None) -> FastAPI:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Request logs from HTTP clients would repeat URLs on every provider call; keep them quiet.
    for noisy in ("httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(settings, gateway, llm)
        applied = initialize_database(container.db)
        if applied:
            logger.info("applied schema migrations %s", applied)
        interrupted = container.runs.mark_interrupted()
        if interrupted:
            logger.warning("marked %d interrupted run(s) as failed", interrupted)
        for warning in settings.public_exposure_warnings():
            logger.warning(warning)
        starter = getattr(container.gateway, "start", None)
        if starter is not None:
            await starter()  # spawn the three MCP server processes (stdio)
        app.state.container = container
        try:
            yield
        finally:
            await container.run_manager.shutdown()
            closer = getattr(container.gateway, "aclose", None)
            if closer is not None:
                await closer()

    app = FastAPI(
        title="MarketLens API",
        version=__version__,
        description="Evidence-grounded equity research API. Research only: no trading, no brokerage access, "
        "no personalized recommendations.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-MarketLens-Client"],
    )
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_request_body_bytes)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(research.router)
    app.include_router(workspace.router)
    app.include_router(actions.router)
    app.include_router(voice.router)
    app.include_router(voice.internal)
    app.include_router(compare.router)
    app.include_router(symbols.router)
    app.include_router(analytics.router)
    return app


def _app_from_environment() -> FastAPI:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    return create_app(settings)


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    global _app
    if name == "app":
        if _app is None:
            _app = _app_from_environment()
        return _app
    raise AttributeError(name)
