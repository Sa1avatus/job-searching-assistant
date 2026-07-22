"""Search hh.ru by the candidate's own verified skills/keywords and stage applications for review.

Uses browser automation (adapters.job_boards.headhunter_browser) rather than api.hh.ru: the
anonymous JSON API is CAPTCHA-limited in practice (see docs/known-limitations.md), so both search
and vacancy extraction now read hh.ru's public pages directly through Chromium — no login/session
is required for search or extraction, only for the separate, explicitly confirmed real-submission
step (``POST /v1/applications/{id}/apply-headhunter``).

This only ever creates vacancies and ``awaiting_review`` applications — it never schedules a real
submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge, LoginRequired
from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError, RecruitmentService
from app.storage.tables import ApplicationRow, UserRow, VacancyRow


class LinkedInSessionRequiredError(RuntimeError):
    """LinkedIn search needs a captured session; run scripts/browser_login_capture.py linkedin."""

_DEFAULT_LIMIT = 15
_MAX_LIMIT = 50


class NoSearchKeywordsError(RuntimeError):
    """The candidate has no verified skills/keywords to search with."""


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    application_id: str
    vacancy_id: str
    title: str
    company: str
    source_url: str
    match_score: int
    status: str  # "created" | "already_existed"


class JobDiscoveryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def discover_headhunter_vacancies(
        self,
        user_id: str,
        *,
        headhunter_adapter: HeadHunterBrowserAdapter,
        locations: list[str],
        limit: int = _DEFAULT_LIMIT,
        search_text: str | None = None,
    ) -> list[DiscoveryOutcome]:
        recruitment = RecruitmentService(self._session)
        user = self._session.get(UserRow, user_id)
        if user is None:
            raise EntityNotFoundError("User not found")
        text = (search_text or self._build_search_text(user_facts=user.facts)).strip()
        if not text:
            raise NoSearchKeywordsError(
                "No search keywords available; add verified skill facts or pass search_text"
            )

        limit = min(max(limit, 1), _MAX_LIMIT)
        try:
            hits = await headhunter_adapter.search(
                text=text, location_names=locations, limit=limit
            )
        except (CaptchaChallenge, ApplyBlocked):
            # A CAPTCHA/blocked search page mid-run should surface as "found nothing this time"
            # rather than a hard failure; the caller can retry once the challenge is resolved.
            return []

        outcomes: list[DiscoveryOutcome] = []
        for hit in hits:
            outcome = await self._stage_one(
                recruitment, headhunter_adapter, user_id, hit.source_url
            )
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    async def _stage_one(
        self,
        recruitment: RecruitmentService,
        headhunter_adapter: HeadHunterBrowserAdapter,
        user_id: str,
        source_url: str,
    ) -> DiscoveryOutcome | None:
        existing_vacancy = self._session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == source_url)
        )
        if existing_vacancy is None:
            try:
                extracted = await headhunter_adapter.extract_vacancy(source_url)
            except Exception:  # noqa: BLE001 - a single unreadable search hit should not abort the run
                return None
            try:
                vacancy = recruitment.create_vacancy(
                    source_url=extracted.source_url,
                    title=extracted.title,
                    company=extracted.company,
                    required_skills=list(extracted.required_skills),
                    preferred_skills=[],
                    location=extracted.location,
                    description_text=extracted.description_text,
                    adapter_name="headhunter",
                    source_evidence_url=extracted.source_url,
                    application_fields=[
                        {
                            "field_id": field.field_id,
                            "label": field.label,
                            "field_type": field.field_type.value,
                            "is_required": field.is_required,
                            "semantic_category": field.semantic_category,
                        }
                        for field in extracted.form_fields
                    ],
                    requires_sensitive_review=extracted.requires_sensitive_review,
                )
            except DuplicateEntityError:
                vacancy = self._session.scalar(
                    select(VacancyRow).where(VacancyRow.source_url == source_url)
                )
                if vacancy is None:
                    return None
        else:
            vacancy = existing_vacancy

        existing_application = self._session.scalar(
            select(ApplicationRow).where(
                ApplicationRow.user_id == user_id, ApplicationRow.vacancy_id == vacancy.id
            )
        )
        if existing_application is not None:
            return DiscoveryOutcome(
                application_id=existing_application.id,
                vacancy_id=vacancy.id,
                title=vacancy.title,
                company=vacancy.company,
                source_url=vacancy.source_url,
                match_score=existing_application.match_score,
                status="already_existed",
            )

        try:
            application = recruitment.prepare_application(user_id, vacancy.id)
        except (EntityNotFoundError, DuplicateEntityError):
            return None
        return DiscoveryOutcome(
            application_id=application.id,
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            source_url=vacancy.source_url,
            match_score=application.match_score,
            status="created",
        )

    async def discover_linkedin_vacancies(
        self,
        user_id: str,
        *,
        linkedin_adapter: LinkedInBrowserAdapter,
        locations: list[str],
        limit: int = _DEFAULT_LIMIT,
        search_text: str | None = None,
    ) -> list[DiscoveryOutcome]:
        recruitment = RecruitmentService(self._session)
        user = self._session.get(UserRow, user_id)
        if user is None:
            raise EntityNotFoundError("User not found")
        text = (search_text or self._build_search_text(user_facts=user.facts)).strip()
        if not text:
            raise NoSearchKeywordsError(
                "No search keywords available; add verified skill facts or pass search_text"
            )

        limit = min(max(limit, 1), _MAX_LIMIT)
        try:
            hits = await linkedin_adapter.search(text=text, location_names=locations, limit=limit)
        except LoginRequired as error:
            raise LinkedInSessionRequiredError(
                "No usable LinkedIn session; run scripts/browser_login_capture.py linkedin"
            ) from error
        except (CaptchaChallenge, ApplyBlocked):
            return []

        outcomes: list[DiscoveryOutcome] = []
        for hit in hits:
            outcome = await self._stage_linkedin(
                recruitment, linkedin_adapter, user_id, hit.source_url
            )
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    async def _stage_linkedin(
        self,
        recruitment: RecruitmentService,
        linkedin_adapter: LinkedInBrowserAdapter,
        user_id: str,
        source_url: str,
    ) -> DiscoveryOutcome | None:
        existing_vacancy = self._session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == source_url)
        )
        if existing_vacancy is None:
            try:
                extracted = await linkedin_adapter.extract_vacancy(source_url)
            except Exception:  # noqa: BLE001 - a single unreadable search hit should not abort the run
                return None
            if not extracted.has_easy_apply:
                # Real submission is only supported for native Easy Apply jobs (see
                # linkedin_browser.py); still stage it so the person can apply manually via the
                # link, but skip it here to avoid cluttering results with unsupported jobs.
                return None
            try:
                vacancy = recruitment.create_vacancy(
                    source_url=extracted.source_url,
                    title=extracted.title,
                    company=extracted.company,
                    required_skills=[],
                    preferred_skills=[],
                    location=extracted.location,
                    description_text=extracted.description_text,
                    adapter_name="linkedin-reference",
                    source_evidence_url=extracted.source_url,
                    application_fields=[],
                    requires_sensitive_review=False,
                )
            except DuplicateEntityError:
                vacancy = self._session.scalar(
                    select(VacancyRow).where(VacancyRow.source_url == source_url)
                )
                if vacancy is None:
                    return None
        else:
            vacancy = existing_vacancy

        existing_application = self._session.scalar(
            select(ApplicationRow).where(
                ApplicationRow.user_id == user_id, ApplicationRow.vacancy_id == vacancy.id
            )
        )
        if existing_application is not None:
            return DiscoveryOutcome(
                application_id=existing_application.id,
                vacancy_id=vacancy.id,
                title=vacancy.title,
                company=vacancy.company,
                source_url=vacancy.source_url,
                match_score=existing_application.match_score,
                status="already_existed",
            )
        try:
            application = recruitment.prepare_application(user_id, vacancy.id)
        except (EntityNotFoundError, DuplicateEntityError):
            return None
        return DiscoveryOutcome(
            application_id=application.id,
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            source_url=vacancy.source_url,
            match_score=application.match_score,
            status="created",
        )

    @staticmethod
    def _build_search_text(*, user_facts: list) -> str:  # type: ignore[type-arg]
        skill_names = [
            fact.name for fact in user_facts if fact.category == "skill" and fact.is_verified
        ]
        return " ".join(skill_names[:8])
