"""Guards for the two things that broke the browser worker's login window before."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_playwright_version_is_pinned_to_the_base_image_tag() -> None:
    dockerfile = (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")

    assert "ARG PLAYWRIGHT_VERSION=" in dockerfile
    # one variable drives both the image tag and the pip package, so they cannot drift apart
    assert "playwright/python:v${PLAYWRIGHT_VERSION}-noble" in dockerfile
    assert 'pip install "playwright==${PLAYWRIGHT_VERSION}"' in dockerfile
    default = re.search(r"ARG PLAYWRIGHT_VERSION=(\S+)", dockerfile)
    assert default and re.fullmatch(r"\d+\.\d+\.\d+", default.group(1))


def test_display_stack_starts_in_dependency_order() -> None:
    script = (ROOT / "scripts" / "start_browser_worker_with_desktop.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/bin/bash")  # the port probe uses bash's /dev/tcp
    order = [
        script.index(marker)
        for marker in (
            "Xvfb ",
            "xdpyinfo -display",
            "x11vnc -display",
            "websockify --web",
            "exec python -m",
        )
    ]
    assert order == sorted(order)
    assert "sleep 1\n" not in script  # a fixed sleep is what raced on slow starts
    assert "|| exit 1" in script  # no display: fail and restart rather than run half-working
    assert "x11-utils" in (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")
