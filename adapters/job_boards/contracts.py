from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from app.browser.engine import BrowserActionResult
from app.domain.forms import FormField


@dataclass(frozen=True, slots=True)
class ReviewPreparation:
    fields: tuple[FormField, ...]
    actions: tuple[BrowserActionResult, ...]

    @property
    def is_ready_for_review(self) -> bool:
        return bool(self.actions) and all(action.is_successful for action in self.actions)


class JobBoardAdapter(Protocol):
    name: str

    def supports_url(self, url: str) -> bool: ...

    async def discover_form_fields(self, url: str) -> tuple[FormField, ...]: ...

    async def prepare_review(
        self, url: str, answers: Mapping[str, str | bool | None]
    ) -> ReviewPreparation: ...


@dataclass(frozen=True, slots=True)
class AtsDetection:
    adapter_name: str
    confidence: float
    evidence: str


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    name: str
    vacancy_extraction: str
    authentication_requirement: str
    form_discovery: str
    file_upload_behavior: str
    validation_detection: str
    submission_detection: str
    confirmation_extraction: str
    submission_supported: bool
    known_limitations: tuple[str, ...]


KNOWN_ATS_HOSTS: dict[str, str] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "hh.ru": "headhunter",
    "www.linkedin.com": "linkedin-reference",
}


ADAPTER_CAPABILITIES: tuple[AdapterCapabilities, ...] = (
    AdapterCapabilities(
        name="greenhouse",
        vacancy_extraction="official_public_api",
        authentication_requirement="none_for_read; secret_api_key_for_write",
        form_discovery="accessible_browser_form",
        file_upload_behavior="validated_browser_upload_before_review",
        validation_detection="typed_html_constraints_before_fill",
        submission_detection="not_implemented",
        confirmation_extraction="not_implemented",
        submission_supported=False,
        known_limitations=("No authenticated application POST", "No live external fill smoke"),
    ),
    AdapterCapabilities(
        name="headhunter",
        vacancy_extraction="browser_dom_automation",
        authentication_requirement="none_for_public_search; user_captured_session_for_apply",
        form_discovery="browser_dom_resume_and_cover_letter_requirements",
        file_upload_behavior="review_only_local_cv_selection",
        validation_detection="typed_api_schema_and_test_flag",
        submission_detection="browser_automation_opt_in",
        confirmation_extraction="dom_confirmation_banner_screenshot",
        submission_supported=True,
        known_limitations=(
            "Public browser search may be CAPTCHA-limited",
            "Site terms prohibit automated page parsing and structured collection",
            "Real submission requires APP_ENABLE_HEADHUNTER_APPLY=true and a session captured "
            "via scripts/browser_login_capture.py; automation never types a password",
            "CAPTCHA/verification checkpoints stop at a human review checkpoint, never solved "
            "automatically",
            "Selectors target hh.ru's data-qa attributes and have not been exercised against the "
            "live site from the development sandbox; verify locally before unattended use",
            "Automating hh.ru remains against the site's stated terms; the account owner accepts "
            "that risk explicitly by enabling the feature flag",
        ),
    ),
    AdapterCapabilities(
        name="linkedin-reference",
        vacancy_extraction="browser_dom_automation_with_manual_reference_fallback",
        authentication_requirement=(
            "approved_partner_oauth_required_for_network_api; "
            "user_captured_browser_session_for_easy_apply"
        ),
        form_discovery="dom_discovery_within_easy_apply_modal_only",
        file_upload_behavior="not_supported",
        validation_detection="strict_reference_url_validation",
        submission_detection="browser_automation_opt_in_easy_apply_only",
        confirmation_extraction="dom_confirmation_banner_screenshot",
        submission_supported=True,
        known_limitations=(
            "Search and extraction use the signed-in browser DOM; no job-seeker API is used",
            "Partner API integration requires written approval and test credentials",
            "Real submission requires APP_ENABLE_LINKEDIN_APPLY=true and a session captured via "
            "scripts/browser_login_capture.py; automation never types a password",
            "Jobs without a native Easy Apply control (external redirect) are not attempted",
            "CAPTCHA/verification checkpoints stop at a human review checkpoint, never solved "
            "automatically; no fingerprint spoofing or proxy rotation is used",
            "Selectors have not been exercised against the live site from the development "
            "sandbox; verify locally before unattended use",
            "LinkedIn's User Agreement prohibits this kind of automation and actively detects "
            "it; the account owner accepts the risk of restriction/ban explicitly by enabling "
            "the feature flag",
        ),
    ),
)


def detect_ats(url: str) -> AtsDetection:
    hostname = (urlparse(url).hostname or "").casefold()
    for known_host, adapter_name in KNOWN_ATS_HOSTS.items():
        if hostname == known_host or hostname.endswith(f".{known_host}"):
            return AtsDetection(adapter_name, 1.0, f"hostname:{known_host}")
    return AtsDetection("generic", 0.25, "no known ATS hostname")
