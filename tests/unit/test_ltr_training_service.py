"""LTR training guards and shadow scoring on a real (sqlite) database (stage 3A)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.matching.cross_encoder.annotation import load_features_from_db, submit_pointwise
from app.matching.ltr.schema import ModelArtifact, ModelArtifactError
from app.services.annotation_dataset import create_split, freeze_split
from app.services.ltr_training import (
    LtrDisabled,
    LtrNotReady,
    LtrScorer,
    build_groups,
    run_shadow,
    train_and_evaluate,
)
from app.storage.database import Base
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

N = 80


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(UserRow(id="u1", display_name="U", active_cv_file_id=None))
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
    session.flush()
    session.get(UserRow, "u1").active_cv_file_id = "cv1"
    for i in range(N):
        session.add(
            VacancyRow(
                id=f"v{i:03d}",
                source_url=f"https://e.test/{i}",
                title=f"Job {i}",
                company=f"Company {i % 25}",
            )
        )
    session.flush()
    for i in range(N):
        application = ApplicationRow(user_id="u1", vacancy_id=f"v{i:03d}", match_score=i)
        session.add(application)
        session.flush()
        session.add(
            ApplicationMatchResultRow(
                application_id=application.id, status="scored", final_score=float(i)
            )
        )
    session.commit()
    return session


def _label_and_freeze(db, count=N):
    labels = ("relevant", "maybe", "not_relevant")
    for i in range(count):
        submit_pointwise(db, "u1", "cv1", f"v{i:03d}", labels[i % 3], [], None)
    create_split(db, "gold")
    db.commit()
    freeze_split(db, "gold")
    db.commit()


def test_training_requires_a_frozen_split(db) -> None:
    create_split(db, "gold")
    db.commit()

    with pytest.raises(LtrNotReady, match="Freeze"):
        train_and_evaluate(db, split_name="gold", output_path="unused.json")


def test_training_refuses_an_incomplete_dataset(db, tmp_path) -> None:
    _label_and_freeze(db)  # 80 labels, one resume, no pairs: far from ready

    with pytest.raises(LtrNotReady, match="not ready"):
        train_and_evaluate(db, split_name="gold", output_path=tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()


def test_forced_training_yields_a_provisional_model_trained_on_the_train_fold(db, tmp_path) -> None:
    _label_and_freeze(db)

    outcome = train_and_evaluate(
        db,
        split_name="gold",
        output_path=tmp_path / "m.json",
        allow_incomplete=True,
        include_lambdamart=False,
    )

    artifact = ModelArtifact.load(outcome.artifact_path, allow_provisional=True)
    assert outcome.provisional and artifact.provisional
    assert artifact.trained_on["eval_fold"] == "validation"
    assert artifact.trained_on["test_evaluated"] is False
    assert artifact.trained_on["dataset_hash"] and outcome.warnings
    train = build_groups(db, split_name="gold", fold="train")
    assert artifact.trained_on["train_items"] == sum(len(g.items) for g in train)
    with pytest.raises(ModelArtifactError, match="provisional"):
        ModelArtifact.load(outcome.artifact_path)  # inference default refuses it


def test_the_test_fold_is_only_scored_on_an_explicit_final_run(db, tmp_path) -> None:
    _label_and_freeze(db)

    final = train_and_evaluate(
        db,
        split_name="gold",
        output_path=tmp_path / "final.json",
        allow_incomplete=True,
        final_test=True,
        include_lambdamart=False,
    )

    assert final.benchmark.fold == "test"
    saved = ModelArtifact.load(final.artifact_path, allow_provisional=True)
    assert saved.trained_on["test_evaluated"] is True


def test_train_and_eval_folds_share_no_vacancy(db) -> None:
    _label_and_freeze(db)

    train = {
        i.vacancy_id for g in build_groups(db, split_name="gold", fold="train") for i in g.items
    }
    validation = {
        i.vacancy_id
        for g in build_groups(db, split_name="gold", fold="validation")
        for i in g.items
    }
    test = {i.vacancy_id for g in build_groups(db, split_name="gold", fold="test") for i in g.items}

    assert train and (train | validation | test)
    assert not train & validation and not train & test and not validation & test


# -- shadow scoring ----------------------------------------------------------


def _trained(db, tmp_path, provisional=True):
    _label_and_freeze(db)
    outcome = train_and_evaluate(
        db,
        split_name="gold",
        output_path=tmp_path / "m.json",
        allow_incomplete=True,
        include_lambdamart=False,
    )
    return outcome.artifact_path


def test_shadow_is_disabled_by_default(db, tmp_path) -> None:
    path = _trained(db, tmp_path)
    settings = Settings(_env_file=None, ltr_model_path=path)

    assert settings.ltr_enabled is False
    with pytest.raises(LtrDisabled):
        run_shadow(db, settings, user_id="u1", resume_id="cv1")


def test_shadow_refuses_a_provisional_model_unless_allowed(db, tmp_path) -> None:
    path = _trained(db, tmp_path)
    settings = Settings(_env_file=None, ltr_enabled=True, ltr_model_path=path)

    with pytest.raises(ModelArtifactError, match="provisional"):
        run_shadow(db, settings, user_id="u1", resume_id="cv1")


def test_shadow_writes_only_shadow_columns(db, tmp_path) -> None:
    path = _trained(db, tmp_path)
    settings = Settings(
        _env_file=None, ltr_enabled=True, ltr_model_path=path, ltr_allow_provisional=True
    )
    before = {
        r.application_id: (r.final_score, r.status)
        for r in db.query(ApplicationMatchResultRow).all()
    }

    summary = run_shadow(db, settings, user_id="u1", resume_id="cv1", top_k=10)
    db.commit()

    assert summary["scored"] == N and 0 <= summary["overlap"] <= 10
    rows = db.query(ApplicationMatchResultRow).all()
    assert {r.application_id: (r.final_score, r.status) for r in rows} == before
    assert all(r.ltr_status == "shadow" and r.ltr_score is not None for r in rows)
    assert sorted(r.ltr_rank for r in rows) == list(range(1, N + 1))  # a proper permutation
    assert all(r.rank_delta is not None for r in rows)


def test_top_n_is_deterministic_and_bounded(db, tmp_path) -> None:
    path = _trained(db, tmp_path)
    scorer = LtrScorer(ModelArtifact.load(path, allow_provisional=True))
    candidates = [(f.vacancy_id, f) for f in load_features_from_db(db, "u1", "cv1")]

    first = scorer.rank_top_n(candidates, 5)
    second = scorer.rank_top_n(list(reversed(candidates)), 5)

    assert first == second and len(first) == 5
    assert [s for _, s in first] == sorted((s for _, s in first), reverse=True)
