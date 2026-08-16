from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.services.application_email_events import ApplicationEmailEventService
from app.services.email_classification import EmailClassifier
from app.services.email_vacancy_matcher import EmailVacancyMatcher
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import UserRow


@dataclass(frozen=True, slots=True)
class ApplicationEmailMessage:
    subject: str
    body: str
    application_id: str | None = None
    company: str | None = None
    vacancy_title: str | None = None


class ApplicationEmailProvider(Protocol):
    async def fetch_messages(self) -> list[ApplicationEmailMessage]: ...


@dataclass(frozen=True, slots=True)
class ApplicationEmailSyncSummary:
    processed: int
    created: int
    duplicates: int
    status_updated: int
    unmatched: int
    unknown: int
    needs_review: int
    failed: int


class ApplicationEmailSyncService:
    def __init__(
        self,
        session: Session,
        *,
        classifier: EmailClassifier | None = None,
        matcher: EmailVacancyMatcher | None = None,
    ) -> None:
        self._session = session
        self._events = ApplicationEmailEventService(
            session,
            classifier=classifier,
            matcher=matcher,
        )

    async def synchronize(
        self,
        user_id: str,
        provider: ApplicationEmailProvider,
    ) -> ApplicationEmailSyncSummary:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
        try:
            messages = await provider.fetch_messages()
        except Exception:  # noqa: BLE001 - provider failure is reported in the summary
            return ApplicationEmailSyncSummary(0, 0, 0, 0, 0, 0, 0, 1)

        processed = created = duplicates = status_updated = unmatched = unknown = 0
        needs_review = failed = 0
        for message in messages:
            processed += 1
            try:
                if self._events.has_llm:
                    result = await self._events.ingest_async(
                        user_id,
                        message.subject,
                        message.body,
                        application_id=message.application_id,
                        company=message.company,
                        vacancy_title=message.vacancy_title,
                    )
                else:
                    result = self._events.ingest(
                        user_id,
                        message.subject,
                        message.body,
                        application_id=message.application_id,
                        company=message.company,
                        vacancy_title=message.vacancy_title,
                    )
            except Exception:  # noqa: BLE001 - one malformed message must not abort the batch
                failed += 1
                continue
            if result.created:
                created += 1
            else:
                duplicates += 1
            if result.status_updated:
                status_updated += 1
            if result.event.application_id is None:
                unmatched += 1
            if result.event.outcome == "unknown":
                unknown += 1
            if result.event.needs_review:
                needs_review += 1
        return ApplicationEmailSyncSummary(
            processed=processed,
            created=created,
            duplicates=duplicates,
            status_updated=status_updated,
            unmatched=unmatched,
            unknown=unknown,
            needs_review=needs_review,
            failed=failed,
        )
