from __future__ import annotations

from playwright.async_api import Page

from app.domain.site_access import validate_site_access
from app.domain.workflow_schemas import NavigateWorkflowStep


async def execute_navigate_workflow_step(
    page: Page,
    step: NavigateWorkflowStep,
    allowed_hosts: tuple[str, ...],
) -> str:
    validated_target = validate_site_access(
        login_url=step.parameters.url,
        allowed_hosts=list(allowed_hosts),
    )
    await page.goto(
        validated_target.login_url,
        wait_until="domcontentloaded",
        timeout=step.timeout_ms,
    )
    validated_result = validate_site_access(
        login_url=page.url,
        allowed_hosts=list(allowed_hosts),
    )
    return validated_result.login_url
