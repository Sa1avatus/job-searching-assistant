from __future__ import annotations

from dataclasses import dataclass

from adapters.job_boards.headhunter_api import HeadHunterVacancyReference
from adapters.job_boards.linkedin_reference import LinkedInJobReference


@dataclass(frozen=True, slots=True)
class BrowserHandoff:
    platform: str
    canonical_url: str
    mode: str
    automated_actions_supported: bool
    instructions: tuple[str, ...]


def create_browser_handoff(source_url: str) -> BrowserHandoff:
    if "linkedin.com" in source_url.casefold():
        linkedin_reference = LinkedInJobReference.from_url(source_url)
        return BrowserHandoff(
            platform="linkedin",
            canonical_url=linkedin_reference.source_url,
            mode="manual_browser_handoff",
            automated_actions_supported=False,
            instructions=(
                "Open the canonical URL in your normal signed-in browser",
                "Complete login, verification, reading, and application actions manually",
                "Return to the assistant and record the result without copying private page data",
            ),
        )
    hh_reference = HeadHunterVacancyReference.from_url(source_url)
    return BrowserHandoff(
        platform="headhunter",
        canonical_url=hh_reference.source_url,
        mode="manual_browser_handoff",
        automated_actions_supported=False,
        instructions=(
            "Open the canonical URL in your normal signed-in browser",
            "Complete login, CAPTCHA, vacancy review, and response actions manually",
            "Return to the assistant and record the result without automated page parsing",
        ),
    )
