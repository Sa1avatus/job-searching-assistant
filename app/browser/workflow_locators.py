from __future__ import annotations

import json

from playwright.async_api import Locator, Page

from app.domain.workflow_selectors import WorkflowSelectorCandidate


def locator_for_workflow_candidate(page: Page, candidate: WorkflowSelectorCandidate) -> Locator:
    if candidate.kind == "role":
        return page.locator(f"[role={json.dumps(candidate.value)}]")
    if candidate.kind == "label":
        return page.get_by_label(candidate.value, exact=True)
    if candidate.kind == "placeholder":
        return page.get_by_placeholder(candidate.value, exact=True)
    if candidate.kind == "test_id":
        return page.get_by_test_id(candidate.value)
    if candidate.kind == "id":
        return page.locator(f"[id={json.dumps(candidate.value)}]")
    if candidate.kind == "name":
        return page.locator(f"[name={json.dumps(candidate.value)}]")
    if candidate.kind == "css":
        return page.locator(candidate.value)
    raise AssertionError(f"Unsupported workflow selector kind: {candidate.kind}")
