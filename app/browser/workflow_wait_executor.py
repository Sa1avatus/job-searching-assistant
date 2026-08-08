from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.workflow_locators import locator_for_workflow_candidate
from app.domain.workflow_selectors import WorkflowSelectorCandidate
from app.domain.workflow_wait import WaitWorkflowStep


async def execute_wait_workflow_step(
    page: Page, step: WaitWorkflowStep
) -> WorkflowSelectorCandidate:
    candidate_timeout_ms = max(1, step.timeout_ms // len(step.selector_candidates))
    for candidate in step.selector_candidates:
        try:
            await locator_for_workflow_candidate(page, candidate).wait_for(
                state="visible",
                timeout=candidate_timeout_ms,
            )
            return candidate
        except PlaywrightTimeoutError:
            continue
    raise TimeoutError("No workflow selector became visible")
