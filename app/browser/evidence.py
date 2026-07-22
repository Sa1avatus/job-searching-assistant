from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page


@dataclass(frozen=True, slots=True)
class BrowserFailureEvidence:
    screenshot_path: Path
    html_path: Path
    metadata_path: Path


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").casefold()
    return normalized[:60] or "browser-action"


async def capture_browser_failure(
    page: Page,
    *,
    artifact_directory: Path,
    action_name: str,
    target: str,
    error: Exception,
) -> BrowserFailureEvidence:
    failure_id = str(uuid.uuid4())
    failure_directory = artifact_directory / "failures" / failure_id
    failure_directory.mkdir(parents=True, exist_ok=False)
    stem = _safe_name(f"{action_name}-{target}")
    screenshot_path = failure_directory / f"{stem}.png"
    html_path = failure_directory / f"{stem}.html"
    metadata_path = failure_directory / "metadata.json"
    await page.screenshot(path=screenshot_path, full_page=True)
    html_path.write_text(await page.content(), encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "failure_id": failure_id,
                "action_name": action_name,
                "target": target,
                "url": page.url,
                "page_title": await page.title(),
                "error_type": type(error).__name__,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return BrowserFailureEvidence(screenshot_path, html_path, metadata_path)
