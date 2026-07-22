"""Run the API with the correct asyncio event loop policy on Windows.

Playwright launches Chromium as a subprocess. On Windows, asyncio's SelectorEventLoop does not
support subprocesses at all (`NotImplementedError` from `_make_subprocess_transport`); only
ProactorEventLoop does. `python -m uvicorn app.api.main:app --reload` can end up creating a
Selector-backed loop before app/api/main.py's own guard has a chance to run (uvicorn's reload
supervisor and worker subprocess start-up order vary by version). Setting the policy here, before
importing/calling uvicorn at all, is the version of the fix that is guaranteed to take effect.

Usage (replaces `python -m uvicorn app.api.main:app --reload --port 8000`):
    python scripts/run_server.py
    python scripts/run_server.py --port 8000 --no-reload
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="Disable autoreload")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
