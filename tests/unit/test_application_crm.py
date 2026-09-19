"""Application CRM: journey, funnel, statistics and immutability (stage 5A)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domain.crm_stats import distinguishable, rate, score_band, wilson_interval
from app.services.application_crm import ApplicationCrmService
from app.services.recruitment import EntityNotFoundError
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    ApplicationSubmissionRow,
    ApplicationTimelineEventRow,
    CvFileRow,
    ImmutableTimelineEvent,
    UserRow,
    VacancyRow,
)

NOW = datetime(2026, 9, 19, tzinfo=UTC)

# -- statistics --------------------------------------------------------------


def test_wilson_interval_matches_known_values() -> None:
    low, high = wilson_interval(5, 10)
    assert (round(low, 3), round(high, 3)) == (0.237, 0.763)
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and 0.1 < high < 0.2
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_small_samples_are_flagged_and_never_distinguishable() -> None:
    assert rate(3, 5).low_sample is True
    assert rate(30, 100).low_sample is False
    assert distinguishable(rate(1, 5), rate(5, 5)) is False


def test_distinguishable_needs_non_overlapping_intervals() -> None:
    assert distinguishable(rate(2, 100), rate(60, 100)) is True
    assert distinguishable(rate(20, 100), rate(25, 100)) is False


def test_score_bands() -> None:
    assert [score_band(v) for v in (0, 19, 20, 79, 80, 100, None)] == [
        "0-19",
        "0-19",
        "20-39",
        "60-79",
        "80-100",
        "80-100",
        "unscored",
    ]


# -- database ----------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([UserRow(id="u1", display_name="A"), UserRow(id="u2", display_name="B")])
        session.flush()
        session.add(
            CvFileRow(
                id="cv1",
                user_id="u1",
                original_filename="cv.pdf",
                storage_path="/x",
                content_type="application/pdf",
                sha256="cv1",
                size_bytes=1,
            )
        )
        session.commit()
        yield session


_counter = {"n": 0}


def _app(
    db,
    *,
    user="u1",
    source_url="https://hh.ru/vacancy/{n}",
    company="Acme",
    status="saved",
    score=50,
    days_ago=30,
    events=(),
    cv="cv1",
):
    _counter["n"] += 1
    n = _counter["n"]
    vacancy = VacancyRow(
        source_url=source_url.format(n=n),
        title=f"Engineer {n}",
        company=company,
        adapter_name="headhunter",
    )
    db.add(vacancy)
    db.flush()
    application = ApplicationRow(
        user_id=user,
        vacancy_id=vacancy.id,
        status=status,
        match_score=score,
        selected_cv_file_id=cv if user == "u1" else None,
        created_at=NOW - timedelta(days=days_ago),
    )
    db.add(application)
    db.flush()
    for offset, kind, new_value in events:
        db.add(
            ApplicationTimelineEventRow(
                application_id=application.id,
                event_type=kind,
                new_value=new_value,
                source="test",
                occurred_at=NOW - timedelta(days=days_ago - offset),
            )
        )
    db.commit()
    return application.id


def test_journey_lists_each_stage_with_evidence_and_time(db) -> None:
    application_id = _app(
        db,
        status="offer",
        events=[
            (1, "status_change", "submitted"),
            (5, "email_received", "next_stage"),
            (6, "status_change", "interview"),
            (12, "status_change", "offer"),
        ],
    )

    journey = ApplicationCrmService(db).journey("u1", application_id)

    reached = {s["stage"]: s["reached"] for s in journey["stages"]}
    assert reached == {
        "saved": True,
        "submitted": True,
        "responded": True,
        "interview": True,
        "offer": True,
    }
    assert journey["outcome"] == "offer" and journey["source"] == "headhunter"
    assert all(s["at"] for s in journey["stages"])
    stages = {s["stage"]: s for s in journey["stages"]}
    assert stages["submitted"]["evidence"] and stages["interview"]["evidence"]


def test_an_open_application_only_reached_saved(db) -> None:
    application_id = _app(db, status="awaiting_review")

    journey = ApplicationCrmService(db).journey("u1", application_id)

    assert [s["stage"] for s in journey["stages"] if s["reached"]] == ["saved"]
    assert journey["outcome"] == "open"


def test_the_journey_is_owner_scoped(db) -> None:
    application_id = _app(db, user="u2")

    with pytest.raises(EntityNotFoundError):
        ApplicationCrmService(db).journey("u1", application_id)
    with pytest.raises(EntityNotFoundError):
        ApplicationCrmService(db).journey("u1", "missing")


def test_funnel_is_owner_scoped(db) -> None:
    _app(db, status="submitted", events=[(1, "status_change", "submitted")])
    _app(db, user="u2", status="interview", events=[(1, "status_change", "interview")])

    mine = ApplicationCrmService(db).funnel("u1", "source", now=NOW)
    theirs = ApplicationCrmService(db).funnel("u2", "source", now=NOW)

    assert mine["totals"]["applications"] == 1 and mine["totals"]["submitted"] == 1
    assert theirs["totals"]["interview"]["successes"] == 1
    assert mine["totals"]["interview"]["successes"] == 0


def test_rates_use_only_mature_submissions(db) -> None:
    for _ in range(3):  # sent 30 days ago, never answered
        _app(db, status="submitted", days_ago=30, events=[(1, "status_change", "submitted")])
    for _ in range(2):  # sent 3 days ago: too young to judge
        _app(db, status="submitted", days_ago=3, events=[(0, "status_change", "submitted")])
    _app(
        db,
        status="interview",
        days_ago=3,
        events=[(0, "status_change", "submitted"), (1, "status_change", "interview")],
    )  # young but already answered: counts

    totals = ApplicationCrmService(db).funnel("u1", "source", now=NOW)["totals"]

    assert totals["submitted"] == 6 and totals["mature_submitted"] == 4
    assert totals["pending"] == 2
    assert totals["response"]["successes"] == 1 and totals["response"]["n"] == 4
    assert totals["interview"]["value"] == 0.25


def test_response_needs_an_employer_signal_not_a_manual_update(db) -> None:
    _app(
        db,
        status="submitted",
        events=[(1, "status_change", "submitted"), (2, "manual_update", "relevant")],
    )

    totals = ApplicationCrmService(db).funnel("u1", "source", now=NOW)["totals"]

    assert totals["response"]["successes"] == 0  # human matching feedback is not an outcome


def test_legacy_status_without_events_still_counts(db) -> None:
    _app(db, status="employer_rejected")

    totals = ApplicationCrmService(db).funnel("u1", "source", now=NOW)["totals"]

    assert totals["submitted"] == 1 and totals["rejection"]["successes"] == 1
    assert totals["response"]["successes"] == 1


def test_a_verified_submission_in_the_ledger_counts_as_submitted(db) -> None:
    application_id = _app(db, status="saved")
    db.add(
        ApplicationSubmissionRow(
            application_id=application_id,
            user_id="u1",
            site_key="headhunter",
            state="confirmed",
            verified_by="worker",
        )
    )
    db.commit()

    assert ApplicationCrmService(db).funnel("u1", "source", now=NOW)["totals"]["submitted"] == 1


def test_grouping_dimensions_split_the_data(db) -> None:
    _app(
        db,
        status="submitted",
        company="Big Co",
        score=90,
        events=[(1, "status_change", "submitted")],
    )
    _app(
        db,
        status="submitted",
        company="big co.",
        score=30,
        events=[(1, "status_change", "submitted")],
    )
    _app(
        db,
        status="submitted",
        company="Other",
        score=30,
        events=[(1, "status_change", "submitted")],
    )
    service = ApplicationCrmService(db)

    companies = {
        g["group"]: g["submitted"] for g in service.funnel("u1", "company", now=NOW)["groups"]
    }
    bands = {
        g["group"]: g["submitted"] for g in service.funnel("u1", "score_band", now=NOW)["groups"]
    }
    resumes = {g["group"] for g in service.funnel("u1", "resume", now=NOW)["groups"]}

    assert companies == {"big co": 2, "other": 1}  # normalised names merge
    assert bands == {"80-100": 1, "20-39": 2}
    assert resumes == {"cv.pdf"}


def test_unknown_group_by_is_rejected(db) -> None:
    with pytest.raises(ValueError):
        ApplicationCrmService(db).funnel("u1", "salary")


def test_insights_never_compare_tiny_groups(db) -> None:
    for _ in range(3):
        _app(
            db,
            status="interview",
            source_url="https://hh.ru/vacancy/{n}",
            events=[(1, "status_change", "interview")],
        )

    assert ApplicationCrmService(db).insights("u1", now=NOW)["findings"] == []


def test_insights_report_evidence_and_admit_overlap(db) -> None:
    for i in range(12):  # linkedin: answered often
        _app(
            db,
            source_url="https://www.linkedin.com/jobs/view/1000{n}",
            status="interview" if i < 9 else "submitted",
            events=[(1, "status_change", "submitted")]
            + ([(2, "status_change", "interview")] if i < 9 else []),
        )
    for i in range(12):  # hh: rarely answered
        _app(
            db,
            status="interview" if i < 1 else "submitted",
            events=[(1, "status_change", "submitted")]
            + ([(2, "status_change", "interview")] if i < 1 else []),
        )

    insights = ApplicationCrmService(db).insights("u1", now=NOW)

    source = next(f for f in insights["findings"] if f["dimension"] == "source")
    assert source["best"]["group"] == "linkedin" and source["worst"]["group"] == "headhunter"
    assert source["distinguishable"] is True and source["best"]["n"] == 12
    assert "Descriptive only" in insights["caution"]


# -- immutability --------------------------------------------------------------


def test_timeline_events_cannot_be_modified_or_deleted(db) -> None:
    application_id = _app(db, events=[(1, "status_change", "submitted")])
    event = db.query(ApplicationTimelineEventRow).filter_by(application_id=application_id).one()

    event.new_value = "offer"
    with pytest.raises(ImmutableTimelineEvent):
        db.commit()
    db.rollback()

    db.delete(db.get(ApplicationTimelineEventRow, event.id))
    with pytest.raises(ImmutableTimelineEvent):
        db.commit()
    db.rollback()
    assert db.get(ApplicationTimelineEventRow, event.id).new_value == "submitted"
