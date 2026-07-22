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

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge, LoginRequired
from adapters.job_boards.greenhouse_api import (
    GreenhouseBoardReference,
    GreenhouseJobBoardApi,
    GreenhouseSearchHit,
)
from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter, HeadHunterSearchHit
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter, LinkedInSearchHit
from app.services.company_blacklist import CompanyBlacklistService
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError, RecruitmentService
from app.storage.tables import ApplicationRow, UserRow, VacancyRow


class LinkedInSessionRequiredError(RuntimeError):
    """LinkedIn search needs a signed-in browser session."""


_DEFAULT_LIMIT = 15
_MAX_LIMIT = 50
_MAX_SEARCH_QUERIES = 8


class NoSearchKeywordsError(RuntimeError):
    """The candidate has no verified skills/keywords to search with."""


class GreenhouseDiscoveryError(RuntimeError):
    """No configured Greenhouse board could be searched."""


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    application_id: str
    vacancy_id: str
    title: str
    company: str
    source_url: str
    match_score: int
    status: str  # "created" | "already_existed"


def build_search_queries(search_text: str) -> list[str]:
    """Expand a candidate-entered phrase into a small, site-friendly query set."""
    normalized_text = " ".join(search_text.split()).strip(" ,;|")
    if not normalized_text:
        return []

    phrases = [
        " ".join(phrase.split()) for phrase in re.split(r"[,;|\n]+", search_text) if phrase.strip()
    ]
    candidates = [normalized_text, *phrases]
    for phrase in phrases:
        words = re.findall(r"[\w#+.-]{2,}", phrase, flags=re.UNICODE)
        candidates.extend(words)

    queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized_candidate = candidate.strip(" ,;|")
        key = normalized_candidate.casefold()
        if not normalized_candidate or key in seen:
            continue
        seen.add(key)
        queries.append(normalized_candidate)
        if len(queries) == _MAX_SEARCH_QUERIES:
            break
    return queries


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
        hits_by_url: dict[str, HeadHunterSearchHit] = {}
        for query in build_search_queries(text):
            try:
                query_hits = await headhunter_adapter.search(
                    text=query, location_names=locations, limit=limit
                )
            except (CaptchaChallenge, ApplyBlocked):
                # Preserve results collected before a challenge appeared on a later query.
                break
            for hit in query_hits:
                hits_by_url.setdefault(hit.source_url, hit)
                if len(hits_by_url) == limit:
                    break
            if len(hits_by_url) == limit:
                break

        outcomes: list[DiscoveryOutcome] = []
        for hit in hits_by_url.values():
            outcome = await self._stage_one(
                recruitment, headhunter_adapter, user_id, hit.source_url
            )
            if outcome is not None:
                outcomes.append(outcome)
        return sorted(outcomes, key=lambda outcome: outcome.match_score, reverse=True)

    async def _stage_one(
        self,
        recruitment: RecruitmentService,
        headhunter_adapter: HeadHunterBrowserAdapter,
        user_id: str,
        source_url: str,
    ) -> DiscoveryOutcome | None:
        vacancy: VacancyRow | None = self._session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == source_url)
        )
        if vacancy is None:
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
        if vacancy is None:
            return None
        if CompanyBlacklistService(self._session).contains(user_id, vacancy.company):
            return None

        existing_application = self._session.scalar(
            select(ApplicationRow).where(
                ApplicationRow.user_id == user_id, ApplicationRow.vacancy_id == vacancy.id
            )
        )
        if existing_application is not None:
            if existing_application.status in {"rejected", "skipped"}:
                return None
            self._rescore_from_text(existing_application, vacancy)
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
        self._rescore_from_text(application, vacancy)
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
        hits_by_url: dict[str, LinkedInSearchHit] = {}
        for query in build_search_queries(text):
            try:
                query_hits = await linkedin_adapter.search(
                    text=query, location_names=locations, limit=limit
                )
            except LoginRequired as error:
                raise LinkedInSessionRequiredError(
                    "Сессия LinkedIn истекла. Авторизуйтесь заново в личном кабинете"
                ) from error
            except (CaptchaChallenge, ApplyBlocked):
                break
            for hit in query_hits:
                hits_by_url.setdefault(hit.source_url, hit)
                if len(hits_by_url) == limit:
                    break
            if len(hits_by_url) == limit:
                break

        outcomes: list[DiscoveryOutcome] = []
        for hit in hits_by_url.values():
            outcome = await self._stage_linkedin(
                recruitment, linkedin_adapter, user_id, hit.source_url
            )
            if outcome is not None:
                outcomes.append(outcome)
        return sorted(outcomes, key=lambda outcome: outcome.match_score, reverse=True)

    async def _stage_linkedin(
        self,
        recruitment: RecruitmentService,
        linkedin_adapter: LinkedInBrowserAdapter,
        user_id: str,
        source_url: str,
    ) -> DiscoveryOutcome | None:
        vacancy: VacancyRow | None = self._session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == source_url)
        )
        if vacancy is None:
            try:
                extracted = await linkedin_adapter.extract_vacancy(source_url)
            except Exception:  # noqa: BLE001 - a single unreadable search hit should not abort the run
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
        if vacancy is None:
            return None
        if CompanyBlacklistService(self._session).contains(user_id, vacancy.company):
            return None

        existing_application = self._session.scalar(
            select(ApplicationRow).where(
                ApplicationRow.user_id == user_id, ApplicationRow.vacancy_id == vacancy.id
            )
        )
        if existing_application is not None:
            if existing_application.status in {"rejected", "skipped"}:
                return None
            self._rescore_from_text(existing_application, vacancy)
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
        self._rescore_from_text(application, vacancy)
        return DiscoveryOutcome(
            application_id=application.id,
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            source_url=vacancy.source_url,
            match_score=application.match_score,
            status="created",
        )

    async def discover_greenhouse_vacancies(
        self,
        user_id: str,
        *,
        greenhouse_adapter: GreenhouseJobBoardApi,
        board_urls: list[str],
        locations: list[str],
        limit: int = _DEFAULT_LIMIT,
        search_text: str | None = None,
    ) -> list[DiscoveryOutcome]:
        recruitment = RecruitmentService(self._session)
        user = self._session.get(UserRow, user_id)
        if user is None:
            raise EntityNotFoundError("User not found")
        text = (search_text or self._build_search_text(user_facts=user.facts)).strip()
        queries = build_search_queries(text)
        if not queries:
            raise NoSearchKeywordsError(
                "No search keywords available; add verified skill facts or pass search_text"
            )
        if not board_urls:
            board_urls = self.known_greenhouse_board_urls()
        if not board_urls:
            raise GreenhouseDiscoveryError(
                "Добавьте хотя бы одну ссылку на доску Greenhouse в настройках поиска"
            )

        limit = min(max(limit, 1), _MAX_LIMIT)
        normalized_queries = [query.casefold() for query in queries]
        normalized_locations = [location.casefold() for location in locations if location.strip()]
        hits_by_url: dict[str, GreenhouseSearchHit] = {}
        failed_boards = 0
        for board_url in dict.fromkeys(board_urls):
            try:
                board_hits = await greenhouse_adapter.list_jobs(board_url)
            except Exception:  # noqa: BLE001 - continue with other explicitly configured boards
                failed_boards += 1
                continue
            for hit in board_hits:
                searchable_text = " ".join(
                    (hit.title, hit.company, hit.location, hit.description_text)
                ).casefold()
                if not any(query in searchable_text for query in normalized_queries):
                    continue
                if normalized_locations and not any(
                    location in hit.location.casefold() for location in normalized_locations
                ):
                    continue
                hits_by_url.setdefault(hit.source_url, hit)
                if len(hits_by_url) == limit:
                    break
            if len(hits_by_url) == limit:
                break
        if failed_boards == len(set(board_urls)):
            raise GreenhouseDiscoveryError("Не удалось прочитать указанные доски Greenhouse")

        outcomes: list[DiscoveryOutcome] = []
        for hit in hits_by_url.values():
            outcome = await self._stage_greenhouse(
                recruitment, greenhouse_adapter, user_id, hit.source_url
            )
            if outcome is not None:
                outcomes.append(outcome)
        return sorted(outcomes, key=lambda outcome: outcome.match_score, reverse=True)

    async def _stage_greenhouse(
        self,
        recruitment: RecruitmentService,
        greenhouse_adapter: GreenhouseJobBoardApi,
        user_id: str,
        source_url: str,
    ) -> DiscoveryOutcome | None:
        vacancy = self._session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == source_url)
        )
        if vacancy is None:
            try:
                extracted = await greenhouse_adapter.extract_job(source_url)
            except Exception:  # noqa: BLE001 - one removed job must not abort the board search
                return None
            if CompanyBlacklistService(self._session).contains(user_id, extracted.company):
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
                    adapter_name="greenhouse",
                    source_evidence_url=extracted.evidence_api_url,
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
        if vacancy is None or CompanyBlacklistService(self._session).contains(
            user_id, vacancy.company
        ):
            return None
        existing_application = self._session.scalar(
            select(ApplicationRow).where(
                ApplicationRow.user_id == user_id,
                ApplicationRow.vacancy_id == vacancy.id,
            )
        )
        if existing_application is not None:
            if existing_application.status in {"rejected", "skipped"}:
                return None
            self._rescore_from_text(existing_application, vacancy)
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
        self._rescore_from_text(application, vacancy)
        return DiscoveryOutcome(
            application_id=application.id,
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            source_url=vacancy.source_url,
            match_score=application.match_score,
            status="created",
        )

    def known_greenhouse_board_urls(self) -> list[str]:
        board_urls: list[str] = []
        seen_tokens: set[str] = set()
        source_urls = self._session.scalars(
            select(VacancyRow.source_url).where(VacancyRow.adapter_name == "greenhouse")
        )
        for source_url in source_urls:
            try:
                reference = GreenhouseBoardReference.from_url(source_url)
            except ValueError:
                continue
            if reference.board_token in seen_tokens:
                continue
            seen_tokens.add(reference.board_token)
            board_urls.append(reference.board_url)
        return board_urls

    def _rescore_from_text(self, application: ApplicationRow, vacancy: VacancyRow) -> None:
        user = self._session.get(UserRow, application.user_id)
        if user is None:
            return
        skill_names = [
            fact.name.strip()
            for fact in user.facts
            if fact.category == "skill" and fact.is_verified and fact.name.strip()
        ]
        if not skill_names:
            return
        title = vacancy.title.casefold()
        description = (vacancy.description_text or "").casefold()
        matched_skills = [
            skill
            for skill in skill_names
            if skill.casefold() in title or skill.casefold() in description
        ]
        if not matched_skills:
            return
        coverage_score = 85 * len(matched_skills) / len(skill_names)
        title_bonus = 15 if any(skill.casefold() in title for skill in matched_skills) else 0
        application.match_score = max(
            application.match_score,
            min(100, round(coverage_score + title_bonus)),
        )
        self._session.commit()

    @staticmethod
    def _build_search_text(*, user_facts: list) -> str:  # type: ignore[type-arg]
        skill_names = [
            fact.name for fact in user_facts if fact.category == "skill" and fact.is_verified
        ]
        return " ".join(skill_names[:8])
