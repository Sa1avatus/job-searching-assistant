from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.workflow_locators import locator_for_workflow_candidate
from app.domain.workflow_check import CheckWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate


async def execute_check_workflow_step(
    page: Page, step: CheckWorkflowStep, value: bool
) -> WorkflowSelectorCandidate:
    candidate_timeout_ms = max(1, step.timeout_ms // len(step.selector_candidates))
    for candidate in step.selector_candidates:
        locator = locator_for_workflow_candidate(page, candidate)
        try:
            if value:
                await locator.check(timeout=candidate_timeout_ms)
            else:
                await locator.uncheck(timeout=candidate_timeout_ms)
            return candidate
        except PlaywrightTimeoutError:
            continue
    raise TimeoutError("No workflow checkbox could be updated")
