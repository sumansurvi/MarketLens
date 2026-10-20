"""Local stand-in for Vercel's queue delivery (D-98): run research the way Vercel Queues does.

On Vercel, Vercel Queues invokes the private consumer function in ``vercel_backend.queue_consumer``
for every run the backend publishes. Locally, start the SDK's queue server, run the backend in
queue mode against Postgres, and run this script: it polls the local queue and calls the same
subscriber.

    uv run python -m vercel.queue.devserver --port 8790
    export VERCEL_QUEUE_BASE_URL=http://127.0.0.1:8790 VERCEL_QUEUE_TOKEN=local VERCEL_REGION=iad1 \\
           VERCEL_DEPLOYMENT_ID=dpl_local DATABASE_URL=postgresql://…
    uv run uvicorn vercel_backend.asgi:app --port 8011      # the backend, as Vercel loads it
    uv run python scripts/run_queue_consumer.py             # this consumer

See docs/TESTING_GUIDE.md §7.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vercel.queue import poll_and_handle  # noqa: E402

import vercel_backend.queue_consumer as consumer  # noqa: E402 - after the path setup


def main() -> None:
    print(f"Consuming research runs from topic {consumer.TOPIC!r} (Ctrl+C stops)")
    try:
        asyncio.run(poll_and_handle(consumer.run_research, interval=0.5))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
