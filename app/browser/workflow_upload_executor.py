from __future__ import annotations

from pathlib import Path

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.workflow_locators import locator_for_workflow_candidate
from app.domain.workflow_selectors import WorkflowSelectorCandidate
from app.domain.workflow_upload import UploadWorkflowStep


async def execute_upload_workflow_step(
    page: Page, step: UploadWorkflowStep, file_path: Path
) -> WorkflowSelectorCandidate:
    candidate_timeout_ms = max(1, step.timeout_ms // len(step.selector_candidates))
    for candidate in step.selector_candidates:
        locator = locator_for_workflow_candidate(page, candidate)
        try:
            await locator.set_input_files(file_path, timeout=candidate_timeout_ms)
            return candidate
        except PlaywrightTimeoutError:
            continue
    raise TimeoutError("No workflow file input could be updated")
