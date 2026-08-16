from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.application_email_classifier import classify_application_email
from app.services.application_timeline import ApplicationTimelineService
from app.services.email_entity_extraction import extract_email_entities
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow

_APPLICATION_STATUS_BY_OUTCOME = {
    "rejected": "employer_rejected",
    "next_stage": "interview",
    "offer": "offer",
}


@dataclass(frozen=True, slots=True)
class ApplicationEmailEventResult:
    event: ApplicationEmailEventRow
    created: bool
    status_updated: bool


class ApplicationEmailEventService:
    def __init__(
        self,
        session: Session,
        timeline: ApplicationTimelineService | None = None,
        *,
        auto_update_enabled: bool = True,
    ) -> None:
        self._session = session
        self._timeline = timeline or ApplicationTimelineService(session)
        self._auto_update_enabled = auto_update_enabled

    def ingest(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        application_id: str | None = None,
        company: str | None = None,
        vacancy_title: str | None = None,
    ) -> ApplicationEmailEventResult:
        self._require_user(user_id)
        if application_id is None:
            entities = extract_email_entities(subject, body)
            application_id = self.match_application(
                user_id,
                company=company or entities.company,
                vacancy_title=vacancy_title or entities.vacancy_title,
            )
        if application_id is None:
            application_id = self.match_application_text(
                user_id,
                text=f"{subject}\n{body}",
            )
        self._require_owned_application(user_id, application_id)
        fingerprint = _message_fingerprint(subject, body)
        classified_outcome = classify_application_email(subject, body).value
        existing = self._find_by_fingerprint(user_id, fingerprint)
        if existing is not None:
            event_changed = False
            if existing.application_id is None and application_id is not None:
                existing.application_id = application_id
                event_changed = True
            if existing.outcome == "unknown" and classified_outcome != "unknown":
                existing.outcome = classified_outcome
                event_changed = True
            status_updated = self._apply_outcome(existing)
            if event_changed or status_updated:
                self._session.commit()
            return ApplicationEmailEventResult(
                event=existing,
                created=False,
                status_updated=status_updated,
            )

        event = ApplicationEmailEventRow(
            user_id=user_id,
            application_id=application_id,
            message_fingerprint=fingerprint,
            outcome=classified_outcome,
        )
        self._session.add(event)
        if application_id is not None:
            self._timeline.record(
                application_id,
                "email_received",
                new_value=event.outcome,
                detail={"fingerprint": fingerprint},
                source="email_event",
            )
        status_updated = self._apply_outcome(event)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._find_by_fingerprint(user_id, fingerprint)
            if existing is None:
                raise
            return ApplicationEmailEventResult(
                event=existing,
                created=False,
                status_updated=False,
            )
        return ApplicationEmailEventResult(
            event=event,
            created=True,
            status_updated=status_updated,
        )

    def _apply_outcome(self, event: ApplicationEmailEventRow) -> bool:
        if event.status_applied or event.application_id is None:
            return False
        if not self._auto_update_enabled:
            return False
        application_status = _APPLICATION_STATUS_BY_OUTCOME.get(event.outcome)
        if application_status is None:
            return False
        application = self._session.get(ApplicationRow, event.application_id)
        if application is None or application.user_id != event.user_id:
            return False
        previous_status = application.status
        application.status = application_status
        event.status_applied = True
        self._timeline.record_status_change(
            event.application_id,
            previous_status,
            application_status,
            source="email_event",
        )
        return True

    def match_application(
        self,
        user_id: str,
        *,
        company: str | None,
        vacancy_title: str | None,
    ) -> str | None:
        normalized_company = _normalize_company_reference(company)
        normalized_title = _normalize_reference(vacancy_title)
        if normalized_company is None and normalized_title is None:
            return None
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
        ).all()
        matches = [
            application.id
            for application, vacancy in rows
            if (
                normalized_company is None
                or _normalize_company_reference(vacancy.company) == normalized_company
            )
            and (
                normalized_title is None or _normalize_reference(vacancy.title) == normalized_title
            )
        ]
        if len(matches) != 1:
            return None
        return str(matches[0])

    def match_application_text(self, user_id: str, *, text: str) -> str | None:
        normalized_text = _normalize_reference(text)
        if normalized_text is None:
            return None
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
        ).all()
        title_match_ids: list[str] = []
        company_match_ids: list[str] = []
        for application, vacancy in rows:
            company = _normalize_reference(vacancy.company)
            title = _normalize_reference(vacancy.title)
            title_matches = title is not None and len(title) >= 8 and title in normalized_text
            company_matches = (
                company is not None and len(company) >= 5 and company in normalized_text
            )
            if title_matches:
                title_match_ids.append(application.id)
            if company_matches:
                company_match_ids.append(application.id)
        if len(title_match_ids) == 1:
            return title_match_ids[0]
        if title_match_ids:
            return None
        if len(company_match_ids) == 1:
            return company_match_ids[0]
        return None

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")

    def _require_owned_application(self, user_id: str, application_id: str | None) -> None:
        if application_id is None:
            return
        application = self._session.get(ApplicationRow, application_id)
        if application is None or application.user_id != user_id:
            raise EntityNotFoundError("Application not found")

    def _find_by_fingerprint(
        self,
        user_id: str,
        fingerprint: str,
    ) -> ApplicationEmailEventRow | None:
        return self._session.scalar(
            select(ApplicationEmailEventRow).where(
                ApplicationEmailEventRow.user_id == user_id,
                ApplicationEmailEventRow.message_fingerprint == fingerprint,
            )
        )

    def list_review_items(
        self,
        user_id: str,
    ) -> list[ApplicationEmailEventRow]:
        return list(
            self._session.scalars(
                select(ApplicationEmailEventRow)
                .where(
                    ApplicationEmailEventRow.user_id == user_id,
                    (ApplicationEmailEventRow.outcome == "unknown")
                    | (ApplicationEmailEventRow.application_id.is_(None)),
                )
                .order_by(ApplicationEmailEventRow.processed_at.desc())
            ).all()
        )


def _message_fingerprint(subject: str, body: str) -> str:
    normalized = "\n".join((subject.strip(), body.strip())).encode()
    return hashlib.sha256(normalized).hexdigest()


def _normalize_reference(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _normalize_company_reference(value: str | None) -> str | None:
    normalized = _normalize_reference(value)
    if normalized is None:
        return None
    words = normalized.replace("&", " ").replace(".", " ").split()
    ignored_words = {
        "ag",
        "careers",
        "corp",
        "corporation",
        "gmbh",
        "hr",
        "inc",
        "jobs",
        "limited",
        "llc",
        "ltd",
        "plc",
        "recruiter",
        "recruiting",
        "recruitment",
        "team",
    }
    company_words = [word for word in words if word not in ignored_words]
    if company_words[-2:] == ["talent", "acquisition"]:
        company_words = company_words[:-2]
    return " ".join(company_words) or None
