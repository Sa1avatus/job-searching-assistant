"""Combined server: runs the browser-worker task loop alongside the HTTP API.

This is the entry point for the browser-worker container. It starts:
1. The HTTP API (browser operations proxy) on port 8080
2. The background task loop (apply/session-probe workers) via asyncio
"""

from __future__ import annotations

import asyncio
import signal

import uvicorn

from app.observability.logging import configure_logging


async def _run_task_worker() -> None:
    """Run the browser task worker loop in the background."""
    from app.workers.browser_worker import run_worker

    await run_worker()


def main() -> None:
    configure_logging()

    loop = asyncio.new_event_loop()

    # Start the task worker as a background task
    worker_task = loop.create_task(_run_task_worker())

    # Run the HTTP API server in the foreground
    from app.workers.browser_http import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        loop="none",
        log_level="info",
    )
    server = uvicorn.Server(config)

    def _shutdown(*_: object) -> None:
        worker_task.cancel()
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(server.serve())
    finally:
        worker_task.cancel()
        loop.run_until_complete(asyncio.gather(worker_task, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
