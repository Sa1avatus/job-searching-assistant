"""Regression tests for annotation/feedback dataset integrity (stage 2A)."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.annotation import (
    InvalidAnnotation,
    canonicalize_pair,
    limit_bucket_dominance,
    normalize_reasons,
    review_priority,
    training_pair,
)
from app.matching.cross_encoder.annotation import (
    SampledCandidate,
    _persist,
    _stratified_sample,
    export_dataset,
    get_annotation_queue,
    get_annotation_stats,
    submit_pairwise,
    submit_pointwise,
)
from app.matching.cross_encoder.features import MatchFeatures
from app.storage.database import Base
from app.storage.tables import (
    AnnotationFeedbackRow,
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add_all([UserRow(id="alice", display_name="A"), UserRow(id="bob", display_name="B")])
    session.flush()
    for cv_id, owner in (("cv-alice", "alice"), ("cv-bob", "bob")):
        session.add(
            CvFileRow(
                id=cv_id,
                user_id=owner,
                original_filename=f"{cv_id}.pdf",
                storage_path=f"/x/{cv_id}",
                content_type="application/pdf",
                sha256=cv_id,
                size_bytes=1,
            )
        )
    for vid in ("v1", "v2", "v3"):
        session.add(
            VacancyRow(
                id=vid,
                source_url=f"https://example.test/{vid}",
                title=vid,
                company=f"Co {vid}",
            )
        )
    session.commit()
    return session


def _pointwise(db, vacancy="v1", label="relevant", user="alice", resume="cv-alice", **kwargs):
    result = submit_pointwise(
        db, user, resume, vacancy, label, kwargs.pop("reasons", []), None, **kwargs
    )
    db.commit()
    return result


def _pairwise(db, a="v1", b="v2", preference="a_better", a_reasons=(), b_reasons=(), **kwargs):
    result = submit_pairwise(
        db, "alice", "cv-alice", a, b, preference, list(a_reasons), list(b_reasons), None, **kwargs
    )
    db.commit()
    return result


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(AnnotationFeedbackRow))


# ── ownership ─────────────────────────────────────────────────────────────────


def test_pointwise_rejects_a_resume_the_user_does_not_own(db) -> None:
    result = _pointwise(db, user="alice", resume="cv-bob")

    assert result.status == "forbidden" and _count(db) == 0


def test_pairwise_rejects_a_resume_the_user_does_not_own(db) -> None:
    result = submit_pairwise(db, "bob", "cv-alice", "v1", "v2", "a_better", [], [], None)

    assert result.status == "forbidden" and _count(db) == 0


def test_unknown_vacancy_is_invalid_not_a_crash(db) -> None:
    assert _pointwise(db, vacancy="nope").code == "vacancy_not_found"
    assert _pairwise(db, a="v1", b="nope").code == "vacancy_not_found"


# ── pointwise duplicates ──────────────────────────────────────────────────────


def test_pointwise_resubmit_updates_instead_of_duplicating(db) -> None:
    first = _pointwise(db, label="relevant")
    second = _pointwise(db, label="not_relevant", reasons=["wrong_seniority"])

    assert first.feedback_id == second.feedback_id and _count(db) == 1
    row = db.scalar(select(AnnotationFeedbackRow))
    assert row.label == "not_relevant" and row.reasons == ["wrong_seniority"]


def test_same_vacancy_may_be_labelled_for_different_resumes_and_users(db) -> None:
    _pointwise(db)
    submit_pointwise(db, "bob", "cv-bob", "v1", "maybe", [], None)
    db.commit()

    assert _count(db) == 2


def test_database_rejects_a_logical_pointwise_duplicate(db) -> None:
    _pointwise(db)
    db.add(
        AnnotationFeedbackRow(
            user_id="alice",
            resume_id="cv-alice",
            vacancy_id="v1",
            feedback_type="pointwise",
            label="maybe",
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_concurrent_double_submit_falls_back_to_an_update(db) -> None:
    """The lookup misses (race), the INSERT hits the unique index, the loser updates."""
    winner = _pointwise(db, label="relevant")
    real_row = db.get(AnnotationFeedbackRow, winner.feedback_id)
    lookups = iter([None])  # first lookup: "nothing there yet"

    class Racy:
        def __init__(self, session) -> None:
            self._session = session

        def execute(self, statement):
            result = self._session.execute(statement)
            forced = next(lookups, "real")
            return _NoRow() if forced is None else result

        def __getattr__(self, name):
            return getattr(self._session, name)

    class _NoRow:
        def scalar_one_or_none(self):
            return None

    row = _persist(
        Racy(db),
        select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.id == real_row.id),
        lambda: AnnotationFeedbackRow(
            user_id="alice",
            resume_id="cv-alice",
            vacancy_id="v1",
            feedback_type="pointwise",
            label="maybe",
        ),
        lambda existing: setattr(existing, "label", "maybe"),
    )
    db.commit()

    assert row.id == winner.feedback_id and _count(db) == 1
    assert db.get(AnnotationFeedbackRow, winner.feedback_id).label == "maybe"


# ── pairwise duplicates and schema ────────────────────────────────────────────


def test_reverse_pair_is_the_same_judgement(db) -> None:
    first = _pairwise(db, "v1", "v2", "a_better")
    reverse = _pairwise(db, "v2", "v1", "b_better")

    assert first.feedback_id == reverse.feedback_id and _count(db) == 1


def test_reverse_submission_flips_label_and_reasons_with_it(db) -> None:
    _pairwise(db, "v2", "v1", "a_better", a_reasons=["strong_match"], b_reasons=["far_away"])

    row = db.scalar(select(AnnotationFeedbackRow))
    # canonical order is v1 < v2, so the answer "v2 is better" is stored as b_better
    assert (row.vacancy_a_id, row.vacancy_b_id, row.label) == ("v1", "v2", "b_better")
    assert row.a_reasons == ["far_away"] and row.b_reasons == ["strong_match"]
    assert row.pair_key == "v1:v2"


def test_a_pair_needs_two_different_vacancies(db) -> None:
    assert _pairwise(db, "v1", "v1").code == "same_vacancy"


def test_invalid_labels_and_reasons_are_rejected_with_a_code(db) -> None:
    assert _pointwise(db, label="great").code == "invalid_label"
    assert _pointwise(db, reasons=["Not A Tag"]).code == "invalid_reason"
    assert _pairwise(db, preference="tie").code == "invalid_label"
    assert _pointwise(db, confidence="certain").code == "invalid_confidence"
    assert _count(db) == 0


def test_reasons_are_normalised_and_deduplicated() -> None:
    assert normalize_reasons([" Strong_Match", "strong_match", "remote"]) == [
        "strong_match",
        "remote",
    ]
    with pytest.raises(InvalidAnnotation):
        normalize_reasons([f"r{i}" for i in range(11)])


def test_undecided_pairs_never_become_training_pairs() -> None:
    assert training_pair("v1", "v2", "both_equal") is None
    assert training_pair("v1", "v2", "neither") is None
    assert training_pair("v1", "v2", "a_better") == ("v1", "v2")
    assert training_pair("v1", "v2", "b_better") == ("v2", "v1")


def test_canonicalize_pair_flip_is_an_involution() -> None:
    forward = canonicalize_pair("v1", "v2", "a_better", ["x"], ["y"])
    backward = canonicalize_pair("v2", "v1", "b_better", ["y"], ["x"])

    assert forward == backward


# ── review priority, sampling, diversity ──────────────────────────────────────


def _candidate(vid: str, rank: int, ltr_rank: int | None, company: str = "Co") -> SampledCandidate:
    return SampledCandidate(
        vacancy_id=vid,
        vacancy_title=vid,
        vacancy_company=company,
        vacancy_location="",
        vacancy_description="",
        required_skills=[],
        preferred_skills=[],
        salary_text="",
        work_format="",
        employment_types=[],
        current_rank=rank,
        ltr_rank=ltr_rank,
        current_score=100.0 - rank,
        ltr_score=None if ltr_rank is None else 50.0 - ltr_rank,
        sampling_reason="",
    )


def test_review_priority_is_explainable_bounded_and_ordered() -> None:
    disagreeing = review_priority(current_pct=0.9, ltr_pct=0.1, strata=["rank_disagreement"])
    agreeing = review_priority(current_pct=0.9, ltr_pct=0.9, strata=["current_top"])

    assert 0.0 <= agreeing.score < disagreeing.score <= 1.0
    assert any("disagree" in reason for reason in disagreeing.reasons)
    assert all(isinstance(reason, str) and reason for reason in agreeing.reasons)


def test_queue_order_is_deterministic_with_stable_tie_breaks() -> None:
    pool = [_candidate(f"v{i:03d}", i, i) for i in range(1, 121)]

    first = [c.vacancy_id for c in _stratified_sample(list(pool), limit=60)]
    shuffled = list(reversed(pool))
    second = [c.vacancy_id for c in _stratified_sample(shuffled, limit=60)]

    assert first == second and len(first) == len(set(first))


def test_queue_items_carry_their_priority_reasons() -> None:
    pool = [_candidate(f"v{i:03d}", i, 121 - i, company=f"Co{i}") for i in range(1, 121)]

    sample = _stratified_sample(pool, limit=50)

    assert all(c.priority_reasons and 0 <= c.review_priority <= 1 for c in sample)
    priorities = [c.review_priority for c in sample]
    assert priorities == sorted(priorities, reverse=True)


def test_one_company_cannot_dominate_the_queue() -> None:
    pool = [_candidate(f"a{i:03d}", i, i, company="Big Corp") for i in range(1, 61)]
    pool += [_candidate(f"b{i:03d}", 60 + i, 60 + i, company=f"Small {i}") for i in range(1, 61)]

    sample = _stratified_sample(pool, limit=50)

    assert sum(c.vacancy_company == "Big Corp" for c in sample) <= 15  # 30 % of 50


def test_a_pool_with_too_few_companies_is_not_cut_to_the_cap() -> None:
    kept = limit_bucket_dominance(list(range(40)), bucket=lambda _: "same", limit=40, max_share=0.3)

    assert len(kept) == 40


# ── stats and export ──────────────────────────────────────────────────────────


def test_stats_count_both_vacancies_of_a_pair(db) -> None:
    _pointwise(db, vacancy="v1")
    _pairwise(db, "v2", "v3", "neither")

    stats = get_annotation_stats(db, "alice")

    assert (stats.total_pointwise, stats.total_pairwise) == (1, 1)
    assert stats.pairwise_by_label == {"neither": 1}
    assert stats.unique_vacancies == 3 and stats.unique_resumes == 1


def test_export_is_reproducible_and_excludes_undecided_pairs(db) -> None:
    _pointwise(db, vacancy="v1", label="relevant")
    _pointwise(db, vacancy="v2", label="not_relevant")
    _pairwise(db, "v1", "v2", "a_better")
    _pairwise(db, "v1", "v3", "both_equal")

    first, second = export_dataset(db), export_dataset(db)

    assert first.dataset_hash == second.dataset_hash
    assert [row["gain"] for row in first.pointwise] == [2, 0]
    assert [(p["winner_id"], p["loser_id"]) for p in first.pairs] == [("v1", "v2")]
    assert first.undecided_pairs == 1 and first.issues == []


def test_export_reports_and_skips_ownership_mismatch_and_bad_timestamps(db) -> None:
    good = _pointwise(db, vacancy="v1")
    db.add(
        AnnotationFeedbackRow(
            user_id="alice",
            resume_id="cv-bob",  # resume belongs to bob
            vacancy_id="v2",
            feedback_type="pointwise",
            label="relevant",
        )
    )
    late = _pointwise(db, vacancy="v3")
    row = db.get(AnnotationFeedbackRow, late.feedback_id)
    row.updated_at = row.created_at - timedelta(days=1)
    db.commit()

    export = export_dataset(db)

    assert {issue.code for issue in export.issues} == {"ownership_mismatch", "bad_timestamps"}
    assert [r["vacancy_id"] for r in export.pointwise] == ["v1"] and good.status == "accepted"


def test_export_flags_a_non_canonical_pair(db) -> None:
    db.add(
        AnnotationFeedbackRow(
            user_id="alice",
            resume_id="cv-alice",
            vacancy_id="v2",
            vacancy_a_id="v2",
            vacancy_b_id="v1",
            pair_key="v2:v1",
            feedback_type="pairwise",
            label="a_better",
        )
    )
    db.commit()

    assert [i.code for i in export_dataset(db).issues] == ["non_canonical_pair"]


def test_export_can_be_scoped_to_one_user(db) -> None:
    _pointwise(db)
    submit_pointwise(db, "bob", "cv-bob", "v1", "maybe", [], None)
    db.commit()

    assert len(export_dataset(db, "alice").pointwise) == 1
    assert len(export_dataset(db).pointwise) == 2


def test_provenance_is_recorded(db) -> None:
    _pointwise(db, confidence="high", sampling={"sampling_reason": "ltr_top", "current_rank": 3})

    row = db.scalar(select(AnnotationFeedbackRow))
    assert (row.annotator_id, row.source, row.confidence) == ("alice", "dashboard", "high")
    assert (row.sampling_reason, row.current_rank_at_sampling) == ("ltr_top", 3)


# -- candidate pool ----------------------------------------------------------


def _application(db, user, vacancy, *, cv=None, status="scored", score=50.0):
    application = ApplicationRow(
        user_id=user, vacancy_id=vacancy, selected_cv_file_id=cv, match_score=int(score)
    )
    db.add(application)
    db.flush()
    db.add(
        ApplicationMatchResultRow(application_id=application.id, status=status, final_score=score)
    )
    db.commit()


def _features(*vacancy_ids):
    return [MatchFeatures(resume_id="cv-alice", vacancy_id=v) for v in vacancy_ids]


def test_queue_pool_covers_scored_results_including_implicit_resume(db) -> None:
    db.get(UserRow, "alice").active_cv_file_id = "cv-alice"
    _application(db, "alice", "v1", cv=None, status="scored", score=80)  # implicit: active resume
    _application(db, "alice", "v2", cv="cv-alice", status="degraded", score=60)
    _application(db, "alice", "v3", cv=None, status="failed", score=50)  # placeholder score
    _application(db, "bob", "v1", cv=None, status="scored", score=99)  # another user's
    db.commit()

    queue = get_annotation_queue(db, "alice", "cv-alice", _features("v1", "v2", "v3"), limit=10)

    assert {i.vacancy_id for i in queue.items} == {"v1", "v2"}
    assert queue.total_eligible == 2


def test_implicit_resume_only_applies_to_the_active_resume(db) -> None:
    db.add(
        CvFileRow(
            id="cv-alice-2",
            user_id="alice",
            original_filename="second.pdf",
            storage_path="/x",
            content_type="application/pdf",
            sha256="second",
            size_bytes=1,
        )
    )
    db.get(UserRow, "alice").active_cv_file_id = "cv-alice"
    _application(db, "alice", "v1", cv=None, score=70)
    db.commit()

    queue = get_annotation_queue(db, "alice", "cv-alice-2", _features("v1"), limit=10)

    assert queue.items == []


def test_already_labelled_vacancies_leave_the_queue(db) -> None:
    db.get(UserRow, "alice").active_cv_file_id = "cv-alice"
    _application(db, "alice", "v1", score=80)
    _application(db, "alice", "v2", score=60)
    _pointwise(db, vacancy="v1")

    queue = get_annotation_queue(db, "alice", "cv-alice", _features("v1", "v2"), limit=10)

    assert [i.vacancy_id for i in queue.items] == ["v2"]


def test_queue_for_a_foreign_resume_is_empty(db) -> None:
    _application(db, "bob", "v1", cv="cv-bob", score=80)

    queue = get_annotation_queue(db, "alice", "cv-bob", _features("v1"), limit=10)

    assert queue.items == [] and queue.resume_filename == ""
