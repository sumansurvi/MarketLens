"""Run MarketLens locally: backend API + web app (and optionally the LiveKit voice worker).

Usage: ``uv run python scripts/dev.py [--api-port 8000] [--web-port 3000] [--voice]``
(also ``pnpm dev`` from the repository root). Output from each process is prefixed; Ctrl+C
stops everything, including the MCP server processes the backend started.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def pump(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in iter(process.stdout.readline, ""):
        print(f"[{name}] {line}", end="", flush=True)


def wait_for(url: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - fixed local URL
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _descendants(pid: int) -> list[int]:
    """Every process below ``pid``. Process groups are not enough: pnpm starts Next in its own group."""
    ps = shutil.which("ps")
    if ps is None:
        return []
    listing = subprocess.run([ps, "-ax", "-o", "pid=,ppid="], capture_output=True, text=True, check=False)  # noqa: S603
    children: dict[int, list[int]] = {}
    for line in listing.stdout.splitlines():
        child, parent = (int(x) for x in line.split())
        children.setdefault(parent, []).append(child)
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def stop(processes: dict[str, subprocess.Popen[str]]) -> None:
    """Graceful first (SIGINT lets uvicorn close its MCP sessions), then escalate per process."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl+C must not interrupt cleanup
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    trees = {name: [p.pid, *_descendants(p.pid)] for name, p in processes.items()}
    for sig, grace in ((signal.SIGINT, 15.0), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        alive = [pid for pids in trees.values() for pid in pids if _alive(pid)]
        if not alive:
            break
        for pid in alive:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(_alive(pid) for pid in alive):
            for process in processes.values():
                process.poll()  # reap direct children so they do not linger as zombies
            time.sleep(0.2)
    _sweep_orphaned_mcp_servers()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep_orphaned_mcp_servers() -> None:
    """MCP servers exit when the backend closes their stdin; stop any this repo's backend left behind."""
    venv_python = str(REPO_ROOT / ".venv" / "bin" / "python")
    ps = shutil.which("ps")
    if ps is None:
        return
    time.sleep(1.0)
    listing = subprocess.run([ps, "-ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=False)  # noqa: S603
    for line in listing.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == "1" and venv_python in parts[2] and "--instance mcpgw_" in parts[2]:
            try:
                os.kill(int(parts[0]), signal.SIGTERM)
            except ProcessLookupError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument(
        "--voice", action="store_true", help="also start the LiveKit voice worker (needs voice settings)"
    )
    parser.add_argument("--no-reload", action="store_true", help="run the backend without auto-reload")
    args = parser.parse_args()

    uv, pnpm = shutil.which("uv"), shutil.which("pnpm")
    if not uv or not pnpm:
        print("uv and pnpm are required (see README › Quickstart).", file=sys.stderr)
        return 2
    busy = [p for p in (args.api_port, args.web_port) if port_in_use(p)]
    if busy:
        print(f"Port(s) {busy} are in use. Choose others, e.g. --api-port 8001 --web-port 3001.", file=sys.stderr)
        return 2

    api_url, web_url = f"http://127.0.0.1:{args.api_port}", f"http://localhost:{args.web_port}"
    env = os.environ.copy()
    backend_cmd = [uv, "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.api_port)]
    if not args.no_reload:
        backend_cmd += ["--reload", "--reload-dir", "services"]
    commands = {
        "api": (backend_cmd, {**env, "FRONTEND_ORIGIN": env.get("FRONTEND_ORIGIN", web_url)}),
        "web": (
            [pnpm, "--dir", "apps/web", "exec", "next", "dev", "--port", str(args.web_port)],
            {**env, "BACKEND_URL": api_url},
        ),
    }
    if args.voice:
        # The worker has its own venv; the root one in VIRTUAL_ENV only makes uv print a warning.
        voice_env = {k: v for k, v in env.items() if k != "VIRTUAL_ENV"}
        commands["voice"] = (
            [uv, "run", "--project", "services/voice_agent", "marketlens-voice-agent", "dev"],
            {**voice_env, "VOICE_BACKEND_URL": env.get("VOICE_BACKEND_URL", api_url)},
        )

    processes: dict[str, subprocess.Popen[str]] = {}
    for name, (command, process_env) in commands.items():
        processes[name] = subprocess.Popen(  # noqa: S603 - fixed commands resolved with shutil.which
            command,
            cwd=REPO_ROOT,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,  # own process group, so Ctrl+C cleanup reaches every child
        )
        threading.Thread(target=pump, args=(name, processes[name]), daemon=True).start()

    def shutdown(*_: object) -> None:
        raise KeyboardInterrupt

    # Install both explicitly: a process started in the background inherits "ignore SIGINT".
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        if wait_for(f"{api_url}/api/health", 90) and wait_for(web_url, 120):
            print(
                f"\nMarketLens is running: {web_url}  (API {api_url}/api/health). Press Ctrl+C to stop.\n", flush=True
            )
        while True:
            for name, process in processes.items():
                if process.poll() is not None:
                    print(f"[{name}] exited with status {process.returncode}; stopping.", file=sys.stderr)
                    stop(processes)
                    return process.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping…", flush=True)
        stop(processes)
        return 0


if __name__ == "__main__":
    sys.exit(main())
