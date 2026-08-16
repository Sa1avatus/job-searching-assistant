from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.services.vacancy_metadata import (
    detect_work_format,
    extract_key_skills,
    summarize_vacancy,
)
from app.storage.tables import ApplicationRow, CompanyBlacklistRow, UserRow, VacancyRow


@dataclass(frozen=True, slots=True)
class SavedVacancy:
    application_id: str
    vacancy_id: str
    title: str
    company: str
    source_url: str
    location: str
    match_score: int
    status: str
    source: str
    published_at: datetime | None
    created_at: datetime
    vacancy_summary: str
    work_format: str
    salary_text: str
    employment_types: tuple[str, ...]
    key_skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SavedVacancyPage:
    items: list[SavedVacancy]
    total: int
    page: int
    page_size: int
    total_pages: int


class VacancyCatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_saved_vacancies(
        self,
        user_id: str,
        *,
        query: str = "",
        source: str = "all",
        status: str = "all",
        location: str = "",
        min_match_score: int = 0,
        published_from: date | None = None,
        published_to: date | None = None,
        work_format: str = "all",
        employment_type: str = "all",
        page: int = 1,
        page_size: int = 20,
    ) -> SavedVacancyPage:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")

        statement = (
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(
                ApplicationRow.user_id == user_id,
                ~select(CompanyBlacklistRow.id)
                .where(
                    CompanyBlacklistRow.user_id == user_id,
                    CompanyBlacklistRow.normalized_company
                    == func.lower(func.trim(VacancyRow.company)),
                )
                .exists(),
            )
        )
        statement = self._apply_filters(
            statement,
            query=query,
            source=source,
            status=status,
            location=location,
            min_match_score=min_match_score,
            published_from=published_from,
            published_to=published_to,
            work_format=work_format,
            employment_type=employment_type,
        )
        count_statement = statement.with_only_columns(func.count()).order_by(None)
        total = int(self._session.scalar(count_statement) or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        effective_page = min(page, total_pages)
        rows = self._session.execute(
            statement.order_by(
                ApplicationRow.match_score.desc(),
                ApplicationRow.created_at.desc(),
                ApplicationRow.id,
            )
            .offset((effective_page - 1) * page_size)
            .limit(page_size)
        ).all()

        return SavedVacancyPage(
            items=[
                SavedVacancy(
                    application_id=application.id,
                    vacancy_id=vacancy.id,
                    title=vacancy.title,
                    company=vacancy.company,
                    source_url=vacancy.source_url,
                    location=vacancy.location,
                    match_score=application.match_score,
                    status=application.status,
                    source=self._source_name(vacancy),
                    published_at=vacancy.published_at,
                    created_at=application.created_at,
                    vacancy_summary=summarize_vacancy(vacancy.description_text),
                    work_format=(
                        vacancy.work_format
                        if vacancy.work_format != "unspecified"
                        else detect_work_format(
                            vacancy.title,
                            vacancy.location,
                            vacancy.description_text,
                        )
                    ),
                    salary_text=vacancy.salary_text,
                    employment_types=tuple(vacancy.employment_types or ()),
                    key_skills=extract_key_skills(
                        vacancy.description_text, vacancy.required_skills or ()
                    ),
                )
                for application, vacancy in rows
            ],
            total=total,
            page=effective_page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def reject_saved_vacancy(self, application_id: str) -> ApplicationRow:
        application = self._session.get(ApplicationRow, application_id)
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status in {"submitted", "interview"}:
            raise DuplicateEntityError(
                "Submitted or interview-stage applications cannot be rejected as vacancies"
            )
        application.status = "rejected"
        self._session.commit()
        return application

    @staticmethod
    def _apply_filters(
        statement: Select[tuple[ApplicationRow, VacancyRow]],
        *,
        query: str,
        source: str,
        status: str,
        location: str,
        min_match_score: int,
        published_from: date | None,
        published_to: date | None,
        work_format: str,
        employment_type: str,
    ) -> Select[tuple[ApplicationRow, VacancyRow]]:
        normalized_query = query.strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    VacancyRow.title.ilike(pattern),
                    VacancyRow.company.ilike(pattern),
                    VacancyRow.location.ilike(pattern),
                    VacancyRow.description_text.ilike(pattern),
                )
            )
        if location.strip():
            statement = statement.where(VacancyRow.location.ilike(f"%{location.strip()}%"))
        if status == "all":
            statement = statement.where(
                ApplicationRow.status.not_in(("rejected", "employer_rejected", "skipped"))
            )
        else:
            statement = statement.where(ApplicationRow.status == status)
        if min_match_score:
            statement = statement.where(ApplicationRow.match_score >= min_match_score)
        if published_from is not None:
            statement = statement.where(
                VacancyRow.published_at >= datetime.combine(published_from, time.min, tzinfo=UTC)
            )
        if published_to is not None:
            statement = statement.where(
                VacancyRow.published_at <= datetime.combine(published_to, time.max, tzinfo=UTC)
            )
        if work_format != "all":
            statement = statement.where(VacancyRow.work_format == work_format)
        if employment_type != "all":
            statement = statement.where(
                cast(VacancyRow.employment_types, String).ilike(f'%"{employment_type}"%')
            )
        if source == "headhunter":
            statement = statement.where(VacancyRow.source_url.ilike("%hh.ru/%"))
        elif source == "linkedin":
            statement = statement.where(VacancyRow.source_url.ilike("%linkedin.com/%"))
        elif source == "greenhouse":
            statement = statement.where(VacancyRow.adapter_name == "greenhouse")
        elif source == "registry":
            statement = statement.where(VacancyRow.adapter_name == "google-registry")
        elif source == "other":
            statement = statement.where(
                ~VacancyRow.source_url.ilike("%hh.ru/%"),
                ~VacancyRow.source_url.ilike("%linkedin.com/%"),
                VacancyRow.adapter_name != "google-registry",
                VacancyRow.adapter_name != "greenhouse",
            )
        return statement

    @staticmethod
    def _source_name(vacancy: VacancyRow) -> str:
        source_url = vacancy.source_url.casefold()
        if "hh.ru/" in source_url:
            return "headhunter"
        if "linkedin.com/" in source_url:
            return "linkedin"
        if vacancy.adapter_name == "greenhouse":
            return "greenhouse"
        if vacancy.adapter_name == "google-registry":
            return "registry"
        return "other"
