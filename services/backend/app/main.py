"""FastAPI application factory.

Run: ``uv run uvicorn app.main:app --host 127.0.0.1 --port 8000``. ``app`` is created
lazily on first access so importing this module (e.g. in tests) has no side effects.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NoReturn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from marketlens_core import __version__
from marketlens_core.config import ConfigError, Settings, database_connection_hint, load_settings, on_vercel
from marketlens_core.db import initialize_database

from app.api import actions, analytics, compare, health, research, symbols, voice, workspace
from app.api.errors import backend_misconfigured, install_error_handlers
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
        applied: list[int] = []
        problem: str | None = None
        try:
            applied = initialize_database(container.db)
        except Exception as exc:
            if not on_vercel():
                raise
            logger.exception("MarketLens can't use its database")
            problem = _database_problem(settings, exc)
        if problem is not None:
            # Hosted, a failed startup only shows the platform's generic error page. Keep answering
            # instead: every /api request gets a 503 naming the problem, and the site shows it (D-101).
            app.state.startup_problems = [problem]
            try:
                yield
            finally:
                container.db.close()
            return
        if applied:
            logger.info("applied schema migrations %s", applied)
        if settings.run_execution == "inline":
            # Inline runs lived in the previous process. In queue mode other instances may be
            # running them right now; stale ones are stopped by sweep_stale instead (D-98).
            interrupted = container.runs.mark_interrupted()
            if interrupted:
                logger.warning("marked %d interrupted run(s) as failed", interrupted)
        for warning in settings.deployment_warnings():
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
            container.db.close()

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
    app.add_middleware(
        RequestContextMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
        secure_cookie=settings.session_cookie_secure,
        proxy_secret=settings.proxy_secret,
        site_password=settings.site_password_value,
    )
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


def create_unavailable_app(problems: list[str]) -> FastAPI:
    """A stand-in while the configuration is invalid: every /api request answers 503 with the problems.

    Hosted, a crash at import only shows the platform's generic error page. This keeps answering,
    so the site's red bar names the setting to fix (D-101). It serves no data and runs no tools.
    """
    app = FastAPI(title="MarketLens API (setup incomplete)", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
        response_model=None,
    )
    async def _unavailable(path: str) -> NoReturn:
        raise backend_misconfigured(problems)

    return app


def hosted_app_from_settings() -> FastAPI:
    """The app for a hosted entry point: the real app, or the stand-in when the configuration is invalid."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)  # the runtime logs show the same problems
        return create_unavailable_app(exc.problems)
    return create_app(settings)


_CREDENTIALS = re.compile(r"(://[^/\s:@]*:)[^@\s]*@|(password=)\S+", re.IGNORECASE)


def _database_problem(settings: Settings, exc: Exception) -> str:
    """One readable line about a database the backend can't use, with a known fix when there is one."""
    cause = exc.__cause__ or exc
    first_line = next((line.strip() for line in str(cause).splitlines() if line.strip()), "")
    reason = _CREDENTIALS.sub(lambda m: f"{m.group(1)}***@" if m.group(1) else f"{m.group(2)}***", first_line)[:200]
    problem = f"The backend can't use its database ({type(cause).__name__}{': ' + reason if reason else ''})."
    hint = database_connection_hint(settings.database_url)
    return f"{problem} {hint}" if hint else problem


def _app_from_environment() -> FastAPI:
    if on_vercel():
        return hosted_app_from_settings()
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
