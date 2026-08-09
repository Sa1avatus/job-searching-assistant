import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.services.application_sync import ApplicationStatusSyncService
from app.storage.database import Base
from app.storage.tables import ApplicationRow, UserRow, VacancyRow


class _Probe:
    def __init__(self, outcomes: dict[str, bool | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def has_submitted_application(self, url: str) -> bool:
        self.calls.append(url)
        outcome = self.outcomes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_application_sync_updates_only_confirmed_submissions() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        records = (
            ("headhunter", "https://hh.ru/vacancy/1", "saved"),
            ("linkedin-reference", "https://www.linkedin.com/jobs/view/2", "approved"),
            ("headhunter", "https://hh.ru/vacancy/3", "rejected"),
            ("linkedin-reference", "https://www.linkedin.com/jobs/view/4", "submitted"),
        )
        application_ids: list[str] = []
        for index, (adapter_name, source_url, status) in enumerate(records):
            vacancy = VacancyRow(
                source_url=source_url,
                title=f"Role {index}",
                company="Example",
                adapter_name=adapter_name,
            )
            session.add(vacancy)
            session.flush()
            application = ApplicationRow(
                user_id=user.id,
                vacancy_id=vacancy.id,
                status=status,
                match_score=70,
            )
            session.add(application)
            session.flush()
            application_ids.append(application.id)
        session.commit()

        headhunter = _Probe(
            {
                "https://hh.ru/vacancy/1": True,
                "https://hh.ru/vacancy/3": RuntimeError("unavailable"),
            }
        )
        linkedin = _Probe({"https://www.linkedin.com/jobs/view/2": False})
        summary = asyncio.run(
            ApplicationStatusSyncService(session).synchronize(
                user.id,
                {"headhunter": headhunter, "linkedin-reference": linkedin},
            )
        )

        statuses = {
            application.id: application.status
            for application in session.scalars(
                select(ApplicationRow).where(ApplicationRow.id.in_(application_ids))
            )
        }

    assert summary.checked == 3
    assert summary.updated == 1
    assert summary.unchanged == 1
    assert summary.skipped == 1
    assert summary.failed == 1
    assert [statuses[application_id] for application_id in application_ids] == [
        "submitted",
        "approved",
        "rejected",
        "submitted",
    ]
    assert set(headhunter.calls) == {"https://hh.ru/vacancy/1", "https://hh.ru/vacancy/3"}
    assert linkedin.calls == ["https://www.linkedin.com/jobs/view/2"]
