from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationRow, UserRow, VacancyRow


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
    created_at: datetime


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
        page: int = 1,
        page_size: int = 20,
    ) -> SavedVacancyPage:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")

        statement = (
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
        )
        statement = self._apply_filters(
            statement,
            query=query,
            source=source,
            status=status,
            location=location,
            min_match_score=min_match_score,
        )
        count_statement = statement.with_only_columns(func.count()).order_by(None)
        total = int(self._session.scalar(count_statement) or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        effective_page = min(page, total_pages)
        rows = self._session.execute(
            statement.order_by(ApplicationRow.created_at.desc(), ApplicationRow.id)
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
                    created_at=application.created_at,
                )
                for application, vacancy in rows
            ],
            total=total,
            page=effective_page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @staticmethod
    def _apply_filters(
        statement: Select[tuple[ApplicationRow, VacancyRow]],
        *,
        query: str,
        source: str,
        status: str,
        location: str,
        min_match_score: int,
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
        if status != "all":
            statement = statement.where(ApplicationRow.status == status)
        if min_match_score:
            statement = statement.where(ApplicationRow.match_score >= min_match_score)
        if source == "headhunter":
            statement = statement.where(VacancyRow.source_url.ilike("%hh.ru/%"))
        elif source == "linkedin":
            statement = statement.where(VacancyRow.source_url.ilike("%linkedin.com/%"))
        elif source == "registry":
            statement = statement.where(VacancyRow.adapter_name == "google-registry")
        elif source == "other":
            statement = statement.where(
                ~VacancyRow.source_url.ilike("%hh.ru/%"),
                ~VacancyRow.source_url.ilike("%linkedin.com/%"),
                VacancyRow.adapter_name != "google-registry",
            )
        return statement

    @staticmethod
    def _source_name(vacancy: VacancyRow) -> str:
        source_url = vacancy.source_url.casefold()
        if "hh.ru/" in source_url:
            return "headhunter"
        if "linkedin.com/" in source_url:
            return "linkedin"
        if vacancy.adapter_name == "google-registry":
            return "registry"
        return "other"
