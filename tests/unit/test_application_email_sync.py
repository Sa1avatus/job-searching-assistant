import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.services.application_email_sync import (
    ApplicationEmailMessage,
    ApplicationEmailSyncService,
)
from app.storage.database import Base
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow


class FakeEmailProvider:
    def __init__(self, messages: list[ApplicationEmailMessage]) -> None:
        self._messages = messages

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        return self._messages


class FailingEmailProvider:
    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        raise ConnectionError("mailbox unavailable")


def test_email_sync_processes_batch_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/1",
                    title="Python Engineer",
                    company="Example Corp",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.commit()
            provider = FakeEmailProvider(
                [
                    ApplicationEmailMessage(
                        subject="Next stage",
                        body="Please schedule an interview.",
                        company="Example Corp",
                        vacancy_title="Python Engineer",
                    ),
                    ApplicationEmailMessage(
                        subject="Newsletter",
                        body="Read this week's hiring news.",
                    ),
                ]
            )
            service = ApplicationEmailSyncService(session)

            first = asyncio.run(service.synchronize("user-1", provider))
            second = asyncio.run(service.synchronize("user-1", provider))

            assert first.processed == 2
            assert first.created == 2
            assert first.duplicates == 0
            assert first.status_updated == 1
            assert first.unmatched == 1
            assert first.unknown == 1
            assert first.failed == 0
            assert second.created == 0
            assert second.duplicates == 2
            assert second.status_updated == 0
            assert len(list(session.scalars(select(ApplicationEmailEventRow)))) == 2
            assert session.get(ApplicationRow, "application-1").status == "interview"
    finally:
        engine.dispose()


def test_email_sync_reports_provider_failure() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            summary = asyncio.run(
                ApplicationEmailSyncService(session).synchronize(
                    "user-1",
                    FailingEmailProvider(),
                )
            )

            assert summary.processed == 0
            assert summary.failed == 1
    finally:
        engine.dispose()
