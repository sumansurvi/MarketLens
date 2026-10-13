"""``marketlens-voice-agent [dev|start]``: check configuration, then run the LiveKit agent server.

``dev`` runs with development defaults (no idle process pool, debug logs); ``start`` runs
with production defaults. Both use LiveKit's supported ``python -m livekit.agents start`` path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from marketlens_core.config import load_settings, validate_voice_worker_settings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "dev"
    if mode not in ("dev", "start"):
        print("usage: marketlens-voice-agent [dev|start]", file=sys.stderr)
        return 2
    problems = validate_voice_worker_settings(load_settings(validate=False))
    if problems:
        print("MarketLens voice worker is not configured:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 2
    from livekit.agents.__main__ import main as livekit_main

    worker = str(Path(__file__).with_name("worker.py"))
    return livekit_main(["start", worker, *(["--dev"] if mode == "dev" else [])])


if __name__ == "__main__":
    sys.exit(main())
