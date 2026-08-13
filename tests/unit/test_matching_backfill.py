from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.matching.backfill import MatchingBackfillService
from app.storage.database import Base
from app.storage.tables import ApplicationMatchResultRow, ApplicationRow, UserRow, VacancyRow


def test_matching_backfill_is_owner_scoped_bounded_and_preserves_current_results(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            owner = UserRow(id="owner", display_name="Owner")
            other = UserRow(id="other", display_name="Other")
            vacancies = [
                VacancyRow(
                    id=f"vacancy-{index}",
                    source_url=f"https://example.test/{index}",
                    title="Engineer",
                    company="Example",
                )
                for index in range(1, 6)
            ]
            session.add_all((owner, other, *vacancies))
            session.flush()
            applications = [
                ApplicationRow(
                    id=f"app-{index}",
                    user_id="owner" if index < 5 else "other",
                    vacancy_id=vacancies[index - 1].id,
                    status="draft",
                    match_score=50,
                )
                for index in range(1, 6)
            ]
            session.add_all(applications)
            session.flush()
            session.add_all(
                (
                    ApplicationMatchResultRow(
                        application_id="app-1",
                        status="scored",
                        final_score=81,
                        explanation_json={"matching_source_version": "1", "summary": ["old"]},
                    ),
                    ApplicationMatchResultRow(
                        application_id="app-2",
                        status="scored",
                        final_score=91,
                        explanation_json={"matching_source_version": "2"},
                    ),
                    ApplicationMatchResultRow(
                        application_id="app-3",
                        status="failed",
                        final_score=62,
                        explanation_json={"matching_source_version": "1"},
                    ),
                    ApplicationMatchResultRow(
                        application_id="app-5",
                        status="stale",
                        final_score=70,
                        explanation_json={"matching_source_version": "1"},
                    ),
                )
            )
            session.commit()

            scheduled: list[tuple[str, bool]] = []

            def schedule(_service, application_id: str, *, force: bool = False):
                if application_id == "app-3":
                    raise RuntimeError("provider response body must stay hidden")
                scheduled.append((application_id, force))
                return object()

            monkeypatch.setattr("app.matching.backfill.MatchingJobService.schedule", schedule)
            service = MatchingBackfillService(session)

            first = service.schedule_stale_owner_results("owner", batch_size=2)
            second = service.schedule_stale_owner_results(
                "owner",
                batch_size=2,
                after_application_id=first.next_application_cursor,
            )

            assert scheduled == [("app-1", True)]
            assert first.applications_scanned == 2
            assert first.scheduled == 1
            assert first.skipped_current == 1
            assert first.has_more is True
            assert second.applications_scanned == 2
            assert second.failed == 1
            assert second.skipped_without_result == 1
            assert second.failure_codes == {"RuntimeError": 1}
            assert "provider" not in str(second.as_dict())
            assert second.has_more is False
            aggregate = session.get(ApplicationMatchResultRow, "app-1")
            assert aggregate is not None
            assert aggregate.final_score == 81
            assert aggregate.explanation_json["summary"] == ["old"]
    finally:
        engine.dispose()
