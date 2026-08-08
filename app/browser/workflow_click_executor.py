from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.workflow_locators import locator_for_workflow_candidate
from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate


async def execute_click_workflow_step(
    page: Page, step: ClickWorkflowStep
) -> WorkflowSelectorCandidate:
    candidate_timeout_ms = max(1, step.timeout_ms // len(step.selector_candidates))
    for candidate in step.selector_candidates:
        try:
            await locator_for_workflow_candidate(page, candidate).click(
                timeout=candidate_timeout_ms
            )
            return candidate
        except PlaywrightTimeoutError:
            continue
    raise TimeoutError("No workflow selector could be clicked")
