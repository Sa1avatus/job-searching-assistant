from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.workflow_locators import locator_for_workflow_candidate
from app.domain.workflow_select import SelectWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate


async def execute_select_workflow_step(
    page: Page, step: SelectWorkflowStep, value: str
) -> WorkflowSelectorCandidate:
    candidate_timeout_ms = max(1, step.timeout_ms // len(step.selector_candidates))
    for candidate in step.selector_candidates:
        locator = locator_for_workflow_candidate(page, candidate)
        try:
            await locator.select_option(label=value, timeout=candidate_timeout_ms)
            return candidate
        except PlaywrightTimeoutError:
            continue
    raise TimeoutError("No workflow option could be selected")
