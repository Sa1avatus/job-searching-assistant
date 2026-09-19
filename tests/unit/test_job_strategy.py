"""Closed-loop strategy: evidence gates, decisions, audit trail and follow-up (stage 5B)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.services.job_strategy import (
    MIN_TOTAL_MATURE,
    JobStrategyService,
    RecommendationClosed,
)
from app.services.recruitment import EntityNotFoundError
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    ApplicationTimelineEventRow,
    CvFileRow,
    StrategyRecommendationRow,
    UserRow,
    VacancyRow,
)

NOW = datetime(2026, 9, 19, tzinfo=UTC)
_n = {"i": 0}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([UserRow(id="u1", display_name="A"), UserRow(id="u2", display_name="B")])
        session.flush()
        for cv_id, name, owner in (
            ("cv-good", "strong.pdf", "u1"),
            ("cv-weak", "weak.pdf", "u1"),
            ("cv-other", "other.pdf", "u2"),
        ):
            session.add(
                CvFileRow(
                    id=cv_id,
                    user_id=owner,
                    original_filename=name,
                    storage_path="/x",
                    content_type="application/pdf",
                    sha256=cv_id,
                    size_bytes=1,
                )
            )
        session.commit()
        yield session


def _send(db, *, cv, answered, days_ago=40, user="u1"):
    """One submitted application (optionally answered by the employer)."""
    _n["i"] += 1
    vacancy = VacancyRow(
        source_url=f"https://hh.ru/vacancy/{_n['i']}",
        title=f"Role {_n['i']}",
        company=f"Co {_n['i']}",
        adapter_name="headhunter",
    )
    db.add(vacancy)
    db.flush()
    application = ApplicationRow(
        user_id=user,
        vacancy_id=vacancy.id,
        selected_cv_file_id=cv,
        status="interview" if answered else "submitted",
        match_score=60,
        created_at=NOW - timedelta(days=days_ago),
    )
    db.add(application)
    db.flush()
    db.add(
        ApplicationTimelineEventRow(
            application_id=application.id,
            event_type="status_change",
            new_value="submitted",
            source="test",
            occurred_at=NOW - timedelta(days=days_ago - 1),
        )
    )
    if answered:
        db.add(
            ApplicationTimelineEventRow(
                application_id=application.id,
                event_type="status_change",
                new_value="interview",
                source="test",
                occurred_at=NOW - timedelta(days=days_ago - 5),
            )
        )
    db.commit()


def _seed_clear_difference(db):
    for i in range(20):  # strong resume: 14/20 answered
        _send(db, cv="cv-good", answered=i < 14)
    for i in range(20):  # weak resume: 2/20 answered
        _send(db, cv="cv-weak", answered=i < 2)


# -- evidence gates ----------------------------------------------------------


def test_no_data_yields_no_recommendation_and_says_why(db) -> None:
    result = JobStrategyService(db).generate("u1", now=NOW)

    assert result["created"] == []
    assert f"at least {MIN_TOTAL_MATURE}" in result["no_recommendation_because"][0]
    assert db.query(StrategyRecommendationRow).count() == 0


def test_a_thin_history_is_never_turned_into_advice(db) -> None:
    for _ in range(4):
        _send(db, cv="cv-good", answered=True)
    for _ in range(4):
        _send(db, cv="cv-weak", answered=False)

    result = JobStrategyService(db).generate("u1", now=NOW)

    assert result["created"] == [] and result["matured_submissions"] == 8


def test_overlapping_intervals_are_reported_but_not_recommended(db) -> None:
    for i in range(15):
        _send(db, cv="cv-good", answered=i < 6)  # 40%
    for i in range(15):
        _send(db, cv="cv-weak", answered=i < 4)  # 27%

    result = JobStrategyService(db).generate("u1", now=NOW)

    resume_reasons = [r for r in result["no_recommendation_because"] if r.startswith("By resume")]
    assert result["created"] == [] or all(c["dimension"] != "resume" for c in result["created"])
    assert resume_reasons and "not distinguishable" in resume_reasons[0]


def test_a_clear_difference_becomes_a_recommendation_with_full_evidence(db) -> None:
    _seed_clear_difference(db)

    result = JobStrategyService(db).generate("u1", now=NOW)

    resume = next(c for c in result["created"] if c["dimension"] == "resume")
    assert resume["status"] == "proposed" and resume["kind"] == "prefer_resume"
    assert resume["evidence"]["best"]["group"] == "strong.pdf"
    assert resume["evidence"]["best"]["n"] == 20 and resume["evidence"]["best"]["low"] is not None
    assert resume["evidence"]["causal"] is False and resume["evidence"]["confounders"]
    assert resume["payload"] == {
        "action": "set_active_resume",
        "resume_id": "cv-good",
        "label": "strong.pdf",
    }
    assert "14/20" in resume["statement"] and "do not overlap" in resume["statement"]


def test_generating_nothing_ever_changes_a_setting(db) -> None:
    _seed_clear_difference(db)

    JobStrategyService(db).generate("u1", now=NOW)

    assert db.get(UserRow, "u1").active_cv_file_id is None


def test_generation_is_idempotent(db) -> None:
    _seed_clear_difference(db)
    service = JobStrategyService(db)

    first = service.generate("u1", now=NOW)
    second = service.generate("u1", now=NOW)

    assert first["created"] and second["created"] == []
    assert second["skipped"]
    assert db.query(StrategyRecommendationRow).count() == len(first["created"])


# -- decisions ---------------------------------------------------------------


def _resume_recommendation(db):
    _seed_clear_difference(db)
    service = JobStrategyService(db)
    result = service.generate("u1", now=NOW)
    return service, next(c for c in result["created"] if c["dimension"] == "resume")


def test_accepting_applies_only_the_narrow_action_and_records_it(db) -> None:
    service, rec = _resume_recommendation(db)

    decided = service.decide("u1", rec["id"], decision="accept", note="makes sense", now=NOW)

    assert decided["status"] == "accepted" and decided["decision_note"] == "makes sense"
    assert decided["applied"]["changed"] is True
    assert db.get(UserRow, "u1").active_cv_file_id == "cv-good"
    assert decided["baseline"]["overall_response"]["n"] >= 30
    assert decided["decided_at"]


def test_advice_only_recommendations_change_nothing_when_accepted(db) -> None:
    db.add(  # a second file with the same name makes the resume ambiguous: advice, not an action
        CvFileRow(
            id="cv-good-copy",
            user_id="u1",
            original_filename="strong.pdf",
            storage_path="/y",
            content_type="application/pdf",
            sha256="cv-good-copy",
            size_bytes=1,
        )
    )
    db.commit()
    _seed_clear_difference(db)
    service = JobStrategyService(db)
    created = service.generate("u1", now=NOW)["created"]
    advice = next(c for c in created if c["dimension"] == "resume")
    assert advice["payload"]["action"] == "advice"

    decided = service.decide("u1", advice["id"], decision="accept", now=NOW)

    assert decided["status"] == "accepted"
    assert decided["applied"] == {"action": "advice", "changed": False}
    assert db.get(UserRow, "u1").active_cv_file_id is None


def test_rejecting_is_remembered_and_never_asked_again(db) -> None:
    service, rec = _resume_recommendation(db)

    service.decide("u1", rec["id"], decision="reject", now=NOW)
    again = service.generate("u1", now=NOW)

    assert db.get(UserRow, "u1").active_cv_file_id is None
    assert all(c["dimension"] != "resume" for c in again["created"])
    assert [r["status"] for r in service.list("u1", status="rejected")] == ["rejected"]


def test_a_decision_is_final(db) -> None:
    service, rec = _resume_recommendation(db)
    service.decide("u1", rec["id"], decision="accept", now=NOW)

    with pytest.raises(RecommendationClosed):
        service.decide("u1", rec["id"], decision="reject", now=NOW)
    with pytest.raises(ValueError):
        service.decide("u1", rec["id"], decision="maybe", now=NOW)


def test_recommendations_are_owner_scoped(db) -> None:
    service, rec = _resume_recommendation(db)

    with pytest.raises(EntityNotFoundError):
        service.decide("u2", rec["id"], decision="accept", now=NOW)
    with pytest.raises(EntityNotFoundError):
        service.followup("u2", rec["id"], now=NOW)
    assert service.list("u2") == []
    with pytest.raises(EntityNotFoundError):
        service.generate("nobody", now=NOW)


def test_accepting_refuses_a_resume_that_disappeared(db) -> None:
    service, rec = _resume_recommendation(db)
    db.delete(db.get(CvFileRow, "cv-good"))
    db.commit()

    with pytest.raises(EntityNotFoundError):
        service.decide("u1", rec["id"], decision="accept", now=NOW)
    assert db.get(StrategyRecommendationRow, rec["id"]).status == "proposed"


# -- follow-up ---------------------------------------------------------------


def test_followup_is_only_available_after_acceptance(db) -> None:
    service, rec = _resume_recommendation(db)

    assert service.followup("u1", rec["id"], now=NOW)["state"] == "not_accepted"


def test_followup_waits_for_enough_new_data_then_compares(db) -> None:
    service, rec = _resume_recommendation(db)
    accepted_at = NOW - timedelta(days=30)
    service.decide("u1", rec["id"], decision="accept", now=accepted_at)

    early = service.followup("u1", rec["id"], now=NOW)
    assert (
        early["state"] == "too_early"
        and "matured submissions since the decision" in early["message"]
    )

    for i in range(12):  # applications sent after the decision, all matured, most answered
        _send(db, cv="cv-good", answered=i < 9, days_ago=20)
    later = service.followup("u1", rec["id"], now=NOW)

    assert later["state"] == "compared" and later["causal"] is False
    assert later["after"]["n"] == 12 and later["before"]["n"] == 40
    assert isinstance(later["distinguishable"], bool)
