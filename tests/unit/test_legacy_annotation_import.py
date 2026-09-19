from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.matching.cross_encoder.annotation import export_dataset, submit_pairwise
from app.services.legacy_annotation_import import (
    LEGACY_SOURCE,
    import_legacy_labels,
    rollback_legacy_labels,
)
from app.storage.database import Base
from app.storage.tables import (
    AnnotationFeedbackRow,
    ApplicationTimelineEventRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

ML, ITSM, GONE = "old-ml", "old-itsm", "nobody"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([UserRow(id="u1", display_name="A"), UserRow(id="u2", display_name="B")])
        session.flush()
        for cv, owner in (("cv-now", "u1"), ("cv-other", "u2")):
            session.add(
                CvFileRow(
                    id=cv,
                    user_id=owner,
                    original_filename=f"{cv}.pdf",
                    storage_path="/x",
                    content_type="application/pdf",
                    sha256=cv,
                    size_bytes=1,
                )
            )
        for i in range(6):
            session.add(
                VacancyRow(id=f"v{i}", source_url=f"https://e.test/{i}", title=f"T{i}", company="C")
            )
        session.commit()
        yield session


def _event(db, detail, minute=0):
    db.add(
        ApplicationTimelineEventRow(
            application_id=None if False else _application(db),
            event_type="manual_update",
            new_value=detail.get("preference") or detail.get("label"),
            detail_json=detail,
            source="human_feedback",
            occurred_at=datetime(2026, 8, 20, 4, minute, tzinfo=UTC),
        )
    )
    db.commit()


_apps: list[str] = []


def _application(db):
    from app.storage.tables import ApplicationRow

    if not _apps or db.get(ApplicationRow, _apps[0]) is None:
        _apps.clear()
        application = ApplicationRow(user_id="u1", vacancy_id="v0", match_score=1)
        db.add(application)
        db.flush()
        _apps.append(application.id)
    return _apps[0]


def _pair(resume, a, b, preference, user="u1", source="human", **extra):
    return {
        "type": "matching_feedback",
        "feedback_type": "pairwise",
        "user_id": user,
        "resume_id": resume,
        "vacancy_a_id": a,
        "vacancy_b_id": b,
        "preference": preference,
        "a_reasons": [],
        "b_reasons": [],
        "comment": None,
        "label_source": source,
        "label_confidence": "high",
        "timestamp": "2026-08-20T04:21:51+00:00",
        **extra,
    }


def _point(resume, vacancy, label="relevant"):
    return {
        "type": "matching_feedback",
        "feedback_type": "pointwise",
        "user_id": "u1",
        "resume_id": resume,
        "vacancy_id": vacancy,
        "label": label,
        "reasons": ["skill_mismatch"],
        "comment": None,
        "label_source": "human",
        "label_confidence": "medium",
        "timestamp": "2026-08-20T04:00:00+00:00",
    }


def _seed(db):
    _event(db, _pair(ML, "v1", "v0", "a_better"), 1)  # reversed order on purpose
    _event(db, _pair(ML, "v2", "v3", "neither"), 2)
    _event(db, _point(ML, "v4"), 3)
    _event(db, _pair(ITSM, "v0", "v1", "b_better"), 4)  # another resume: must be ignored
    _event(db, _pair(ITSM, "v4", "v5", "a_better"), 5)


def test_dry_run_reports_and_writes_nothing(db) -> None:
    _seed(db)

    report = import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now")

    assert report.applied is False and report.found_events == 3
    assert dict(report.imported) == {
        "pairwise:b_better": 1,
        "pairwise:neither": 1,
        "pointwise:relevant": 1,
    }
    assert db.scalar(select(func.count()).select_from(AnnotationFeedbackRow)) == 0


def test_only_the_named_source_resume_is_imported(db) -> None:
    _seed(db)

    import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    rows = db.scalars(select(AnnotationFeedbackRow)).all()
    assert len(rows) == 3
    assert {r.resume_id for r in rows} == {"cv-now"}
    assert not any(r.vacancy_id in ("v5",) or r.vacancy_b_id == "v5" for r in rows)  # ITSM's pair


def test_labels_are_canonicalised_with_provenance_and_original_time(db) -> None:
    _seed(db)
    import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    pair = db.scalar(select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.pair_key == "v0:v1"))
    # "v1 is better than v0" shown as (v1, v0): canonical order is v0 < v1, so b_better
    assert (pair.vacancy_a_id, pair.vacancy_b_id, pair.label) == ("v0", "v1", "b_better")
    assert (pair.source, pair.annotator_id, pair.confidence) == (LEGACY_SOURCE, "u1", "high")
    assert pair.created_at.replace(tzinfo=UTC) == datetime(2026, 8, 20, 4, 21, 51, tzinfo=UTC)
    assert pair.updated_at == pair.created_at
    point = db.scalar(
        select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.feedback_type == "pointwise")
    )
    assert point.reasons == ["skill_mismatch"] and point.confidence == "medium"


def test_the_export_accepts_imported_labels_and_drops_undecided_pairs(db) -> None:
    _seed(db)
    import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    export = export_dataset(db, "u1")

    assert export.issues == []
    assert [(p["winner_id"], p["loser_id"]) for p in export.pairs] == [("v1", "v0")]
    assert export.undecided_pairs == 1 and len(export.pointwise) == 1
    assert {p["source"] for p in export.pairs} == {LEGACY_SOURCE}


def test_rerunning_does_not_duplicate(db) -> None:
    _seed(db)
    import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    again = import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    assert again.imported == {} and again.skipped["already_imported"] == 3
    assert db.scalar(select(func.count()).select_from(AnnotationFeedbackRow)) == 3


def test_an_existing_live_label_wins_over_the_legacy_one(db) -> None:
    _seed(db)
    submit_pairwise(db, "u1", "cv-now", "v0", "v1", "a_better", [], [], None)
    db.commit()

    report = import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    assert report.skipped["already_imported"] == 1
    live = db.scalar(select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.pair_key == "v0:v1"))
    assert live.label == "a_better" and live.source == "dashboard"


def test_non_human_and_foreign_and_broken_labels_are_skipped(db) -> None:
    _event(db, _pair(ML, "v0", "v1", "a_better", source="model"), 1)
    _event(db, _pair(ML, "v2", "v3", "a_better", user="u2"), 2)
    _event(db, _pair(ML, "v0", "ghost", "a_better"), 3)
    _event(db, _pair(ML, "v4", "v5", "sideways"), 4)

    report = import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)

    assert report.imported == {}
    assert report.skipped["not_human"] == 1 and report.skipped["other_user"] == 1
    assert report.skipped["vacancy_missing"] == 1
    assert sum(v for k, v in report.skipped.items() if k.startswith("invalid")) == 1


def test_unknown_target_resume_is_refused(db) -> None:
    with pytest.raises(LookupError):
        import_legacy_labels(db, source_resume_id=ML, target_resume_id="missing")


def test_rollback_removes_only_imported_rows(db) -> None:
    _seed(db)
    submit_pairwise(db, "u1", "cv-now", "v4", "v5", "a_better", [], [], None)
    db.commit()
    import_legacy_labels(db, source_resume_id=ML, target_resume_id="cv-now", apply=True)
    db.commit()

    removed = rollback_legacy_labels(db, target_resume_id="cv-now")
    db.commit()

    assert removed == 3
    remaining = db.scalars(select(AnnotationFeedbackRow)).all()
    assert [(r.pair_key, r.source) for r in remaining] == [("v4:v5", "dashboard")]
    # the original timeline events are untouched
    assert db.scalar(select(func.count()).select_from(ApplicationTimelineEventRow)) == 5
