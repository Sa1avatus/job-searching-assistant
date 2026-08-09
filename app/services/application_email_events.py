from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.application_email_classifier import classify_application_email
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow


@dataclass(frozen=True, slots=True)
class ApplicationEmailEventResult:
    event: ApplicationEmailEventRow
    created: bool


class ApplicationEmailEventService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ingest(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        application_id: str | None = None,
    ) -> ApplicationEmailEventResult:
        self._require_user(user_id)
        self._require_owned_application(user_id, application_id)
        fingerprint = _message_fingerprint(subject, body)
        existing = self._find_by_fingerprint(user_id, fingerprint)
        if existing is not None:
            return ApplicationEmailEventResult(event=existing, created=False)

        event = ApplicationEmailEventRow(
            user_id=user_id,
            application_id=application_id,
            message_fingerprint=fingerprint,
            outcome=classify_application_email(subject, body).value,
        )
        self._session.add(event)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._find_by_fingerprint(user_id, fingerprint)
            if existing is None:
                raise
            return ApplicationEmailEventResult(event=existing, created=False)
        return ApplicationEmailEventResult(event=event, created=True)

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


def _message_fingerprint(subject: str, body: str) -> str:
    normalized = "\n".join((subject.strip(), body.strip())).encode()
    return hashlib.sha256(normalized).hexdigest()
