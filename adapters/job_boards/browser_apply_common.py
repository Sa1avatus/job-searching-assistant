"""Shared primitives for browser-automated (non-API) application adapters.

HeadHunter and LinkedIn do not expose a public job-seeker application API, so the only way to
submit an application programmatically is to drive a real signed-in browser session. This module
defines the failure signals those adapters raise so the browser worker can route the task to a
human checkpoint instead of guessing.

Design constraints (see docs/known-limitations.md and docs/adapter-guide.md):
- No credentials are ever typed by the automation. A signed-in session is captured once by the
  user via ``scripts/browser_login_capture.py`` (a headed, human-driven login) and reused as an
  encrypted Playwright storage state. If the session is missing or expired, ``LoginRequired`` is
  raised and the task waits for the user to recapture it.
- No CAPTCHA/verification challenge is solved automatically. ``CaptchaChallenge`` stops the task
  at a screenshot checkpoint for the human to resolve in their own browser.
- These adapters perform a real, irreversible submit action when they succeed. Callers must not
  invoke them without an explicit, separately-confirmed user request.
"""

from __future__ import annotations


class BrowserApplyCheckpoint(RuntimeError):
    """Base class for apply failures that carry optional screenshot evidence for the human."""

    def __init__(self, message: str, *, screenshot_path: str | None = None) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path


class LoginRequired(BrowserApplyCheckpoint):
    """The restored session is not authenticated on the target site."""


class CaptchaChallenge(BrowserApplyCheckpoint):
    """The site presented a CAPTCHA or automated-activity checkpoint."""


class ApplyBlocked(BrowserApplyCheckpoint):
    """The vacancy cannot be safely auto-applied to (already applied, external redirect, etc.)."""
