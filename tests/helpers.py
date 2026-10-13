"""Test helpers shared across suites."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from marketlens_core.config import REPO_ROOT, Settings

FIXTURE_ROOT = REPO_ROOT / "data" / "fixtures" / "v1"


def wait_for_run(client, run_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/research/{run_id}").json()
        if detail["run"]["status"] not in ("queued", "running"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def load_fixture(relative: str) -> dict:
    return json.loads((FIXTURE_ROOT / relative).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- fixture overlays
def fault_payload(shape: str, kind: str, **extra: Any) -> dict:
    """A labeled fixture file that makes the fixture transport raise a provider-style fault."""
    return {
        "_fixture": {
            "is_fixture": True,
            "source_mode": "fixture",
            "dataset_version": "test-overlay",
            "label": "SYNTHETIC TEST OVERLAY",
            "source_timestamp": "2026-09-30T20:00:00Z",
            "shape": shape,
            "fault": {"type": kind, **extra},
        }
    }


def overlay_dataset(root: Path, overrides: dict[str, Any]) -> Path:
    """Copy the fixture dataset and replace files (dict -> JSON, str -> raw text)."""
    target = root / "fixtures-overlay"
    shutil.copytree(FIXTURE_ROOT, target)
    for relative, content in overrides.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- raw MCP client
# Server stderr (SDK INFO logs) is discarded in tests; opened once, synchronously.
_DEVNULL = open(os.devnull, "w")  # noqa: SIM115, PTH123


@asynccontextmanager
async def mcp_session(server_id: str, settings: Settings, *, instance: str = "pytest") -> AsyncIterator[Any]:
    """Spawn one MCP server process exactly as the backend does and connect over stdio."""
    from app.mcp.client_gateway import SERVER_MODULES, server_environment
    from mcp import Client, StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", SERVER_MODULES[server_id], "--instance", instance],
        env=server_environment(settings, server_id),
        cwd=str(REPO_ROOT),
    )
    async with Client(stdio_client(params, errlog=_DEVNULL), read_timeout_seconds=30, cache=None) as client:
        yield client


def structured(result: Any) -> dict:
    assert result.structured_content is not None, result.content
    return result.structured_content


# --------------------------------------------------------------------------- fake upstream HTTP
class FakeUpstream:
    """Local stand-in for Alpha Vantage / SEC used to exercise live transports end to end.

    ``routes`` maps a key (AV ``function`` or a URL path) to a handler returning
    (status, body, delay_seconds). Every request is recorded with its headers and query.
    """

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[str, dict[str, list[str]]], tuple[int, str, float]]] = {}
        self.requests: list[dict[str, Any]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                upstream.requests.append({"path": parsed.path, "query": query, "headers": dict(self.headers)})
                key = query.get("function", [parsed.path])[0]
                handler = upstream.routes.get(key) or upstream.routes.get(parsed.path)
                status, body, delay = handler(parsed.path, query) if handler else (404, "{}", 0.0)
                if delay:
                    time.sleep(delay)
                encoded = body.encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeUpstream:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def count(self, key: str) -> int:
        return sum(1 for r in self.requests if r["query"].get("function", [r["path"]])[0] == key)
