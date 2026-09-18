"""Splits, freezing, coverage report and pair queue (stage 2B)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.annotation_dataset import InvalidSplit
from app.matching.cross_encoder.annotation import (
    get_pair_queue,
    submit_pairwise,
    submit_pointwise,
)
from app.matching.cross_encoder.features import MatchFeatures
from app.services.annotation_dataset import (
    SplitExists,
    SplitFrozen,
    SplitNotFound,
    create_split,
    dataset_report,
    export_fold,
    freeze_split,
)
from app.storage.database import Base
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

N = 60  # vacancies


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(UserRow(id="u1", display_name="U"))
    session.flush()
    for cv in ("cv1", "cv2"):
        session.add(
            CvFileRow(
                id=cv,
                user_id="u1",
                original_filename=f"{cv}.pdf",
                storage_path="/x",
                content_type="application/pdf",
                sha256=cv,
                size_bytes=1,
            )
        )
    for i in range(N):
        session.add(
            VacancyRow(
                id=f"v{i:03d}",
                source_url=f"https://e.test/{i}",
                title=f"Job {i}",
                company=f"Company {i % 20}",  # 3 vacancies per company
            )
        )
    session.commit()
    return session


def _label_all(db, resumes=("cv1",)):
    labels = ("relevant", "maybe", "not_relevant")
    for resume in resumes:
        for i in range(N):
            result = submit_pointwise(
                db,
                "u1",
                resume,
                f"v{i:03d}",
                labels[i % 3],
                [],
                None,
                sampling={
                    "sampling_reason": "rank_disagreement" if i % 5 == 0 else "current_top",
                    "current_rank": 5 if i % 3 == 2 else 40,
                },
            )
            assert result.status == "accepted"
    for i in range(0, 20, 2):
        submit_pairwise(
            db, "u1", resumes[0], f"v{i:03d}", f"v{i + 1:03d}", "a_better", [], [], None
        )
    db.commit()


def test_split_names_are_unique_and_ratios_validated(db) -> None:
    create_split(db, "eval-1")
    db.commit()

    with pytest.raises(SplitExists):
        create_split(db, "eval-1")
    with pytest.raises(InvalidSplit):
        create_split(db, "bad", ratios=(0.5, 0.5, 0.5))
    with pytest.raises(InvalidSplit):
        create_split(db, "   ")


def test_no_group_straddles_two_folds_across_resumes(db) -> None:
    _label_all(db, resumes=("cv1", "cv2"))
    create_split(db, "s")
    db.commit()

    export = export_fold(db, split_name="s")
    fold_by_company: dict[str, set[str]] = {}
    company = {f"v{i:03d}": f"Company {i % 20}" for i in range(N)}
    for row in export["pointwise"]:
        fold_by_company.setdefault(company[row["vacancy_id"]], set()).add(row["fold"])
    # one vacancy labelled under two resumes must sit in one fold as well
    fold_by_vacancy: dict[str, set[str]] = {}
    for row in export["pointwise"]:
        fold_by_vacancy.setdefault(row["vacancy_id"], set()).add(row["fold"])

    assert all(len(folds) == 1 for folds in fold_by_company.values())
    assert all(len(folds) == 1 for folds in fold_by_vacancy.values())
    folds = {r["fold"] for r in export["pointwise"]}
    assert "train" in folds and folds & {"validation", "test"}  # 20 groups: hash decides which


def test_pairs_follow_the_fold_of_their_group(db) -> None:
    _label_all(db)
    create_split(db, "s")
    db.commit()

    export = export_fold(db, split_name="s")
    fold_of = {r["vacancy_id"]: r["fold"] for r in export["pointwise"]}

    assert export["pairs"]
    for pair in export["pairs"]:
        assert fold_of[pair["winner_id"]] == fold_of[pair["loser_id"]] == pair["fold"]


def test_fold_filter_and_unknown_fold(db) -> None:
    _label_all(db)
    create_split(db, "s")
    db.commit()

    train = export_fold(db, split_name="s", fold="train")

    assert train["pointwise"] and {r["fold"] for r in train["pointwise"]} == {"train"}
    with pytest.raises(InvalidSplit):
        export_fold(db, split_name="s", fold="everything")
    with pytest.raises(SplitNotFound):
        export_fold(db, split_name="missing")


def test_freeze_is_immutable_and_records_the_evaluation_vacancies(db) -> None:
    _label_all(db)
    create_split(db, "gold")
    db.commit()

    frozen = freeze_split(db, "gold")
    db.commit()

    assert frozen.frozen_at is not None and frozen.dataset_hash
    assert frozen.eval_vacancies and set(frozen.eval_vacancies.values()) <= {"validation", "test"}
    with pytest.raises(SplitFrozen):
        freeze_split(db, "gold")


def test_freezing_an_empty_dataset_is_refused(db) -> None:
    create_split(db, "empty")
    db.commit()

    with pytest.raises(InvalidSplit):
        freeze_split(db, "empty")


def test_new_labels_after_the_freeze_never_enter_train_for_frozen_groups(db) -> None:
    for i in range(30):
        submit_pointwise(db, "u1", "cv1", f"v{i:03d}", "relevant", [], None)
    db.commit()
    create_split(db, "gold")
    db.commit()
    frozen = freeze_split(db, "gold")
    db.commit()
    frozen_eval = dict(frozen.eval_vacancies)

    for i in range(30, N):  # later labels, including vacancies of frozen companies
        submit_pointwise(db, "u1", "cv1", f"v{i:03d}", "not_relevant", [], None)
    db.commit()

    export = export_fold(db, split_name="gold")
    fold_of = {r["vacancy_id"]: r["fold"] for r in export["pointwise"]}
    frozen_companies = {f"Company {int(v[1:]) % 20}" for v in frozen_eval}
    for vacancy, fold in fold_of.items():
        if f"Company {int(vacancy[1:]) % 20}" in frozen_companies:
            assert fold in {"validation", "test"}, f"{vacancy} leaked into {fold}"
    for vacancy, fold in frozen_eval.items():
        assert fold_of[vacancy] == fold


def test_report_tracks_progress_hard_cases_and_freeze_state(db) -> None:
    _label_all(db)
    create_split(db, "gold")
    db.commit()

    before = dataset_report(db, split_name="gold")
    freeze_split(db, "gold")
    db.commit()
    after = dataset_report(db, split_name="gold")

    assert before["meaningful_labels"] == N + 10 and before["labels_to_go"] == 130
    assert before["hard_negatives"] > 0 and before["model_disagreement"] > 0
    assert any("no frozen evaluation split" in w for w in before["warnings"])
    assert not any("no frozen evaluation split" in w for w in after["warnings"])
    assert after["split"]["frozen"] is True and after["ready"] is False
    assert set(after["by_fold"]) <= {"train", "validation", "test"}


def test_report_is_reproducible(db) -> None:
    _label_all(db)

    assert dataset_report(db)["dataset_hash"] == dataset_report(db)["dataset_hash"]


# -- pair queue --------------------------------------------------------------


def _pool(db, count=8):
    db.get(UserRow, "u1").active_cv_file_id = "cv1"
    for i in range(count):
        application = ApplicationRow(user_id="u1", vacancy_id=f"v{i:03d}", match_score=90 - i)
        db.add(application)
        db.flush()
        db.add(
            ApplicationMatchResultRow(
                application_id=application.id, status="scored", final_score=90.0 - i
            )
        )
    db.commit()
    return [MatchFeatures(resume_id="cv1", vacancy_id=f"v{i:03d}") for i in range(count)]


def test_pair_queue_offers_close_neighbours_once_and_skips_judged_pairs(db) -> None:
    features = _pool(db)
    submit_pairwise(db, "u1", "cv1", "v001", "v000", "b_better", [], [], None)  # reversed order
    db.commit()

    queue = get_pair_queue(db, "u1", "cv1", features, limit=20)

    keys = {tuple(sorted((i.vacancy_a.vacancy_id, i.vacancy_b.vacancy_id))) for i in queue.items}
    assert ("v000", "v001") not in keys  # judged in the opposite order already
    assert len(keys) == len(queue.items) and queue.items
    assert all(i.reason == "close_scores" for i in queue.items)


def test_no_vacancy_is_shown_more_than_twice(db) -> None:
    features = _pool(db, count=10)

    queue = get_pair_queue(db, "u1", "cv1", features, limit=50)

    seen: dict[str, int] = {}
    for item in queue.items:
        for side in (item.vacancy_a, item.vacancy_b):
            seen[side.vacancy_id] = seen.get(side.vacancy_id, 0) + 1
    assert max(seen.values()) <= 2


def test_pair_queue_for_a_foreign_resume_is_empty(db) -> None:
    features = _pool(db)
    db.add(UserRow(id="u2", display_name="Other"))
    db.commit()

    assert get_pair_queue(db, "u2", "cv1", features).items == []
