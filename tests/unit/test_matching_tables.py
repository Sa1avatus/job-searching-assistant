from sqlalchemy import UniqueConstraint

from app.storage.database import Base
from app.storage.tables import ApplicationRow  # noqa: F401


def test_matching_metadata_contains_additive_tables() -> None:
    expected_tables = {
        "vacancy_requirements",
        "candidate_evidence",
        "embedding_records",
        "requirement_matches",
        "application_match_results",
    }

    assert expected_tables <= set(Base.metadata.tables)
    assert "match_score" in Base.metadata.tables["applications"].columns
    assert not Base.metadata.tables["applications"].columns["match_score"].nullable


def test_matching_tables_contain_version_and_explanation_fields() -> None:
    requirement_columns = Base.metadata.tables["vacancy_requirements"].columns
    evidence_columns = Base.metadata.tables["candidate_evidence"].columns
    match_columns = Base.metadata.tables["requirement_matches"].columns
    aggregate_columns = Base.metadata.tables["application_match_results"].columns

    assert {
        "extraction_model",
        "extraction_model_version",
        "extraction_schema_version",
        "extraction_run_id",
        "source_fragment",
    } <= set(requirement_columns.keys())
    assert {
        "cv_file_id",
        "experience_level",
        "is_verified",
        "extraction_run_id",
        "source_fragment",
    } <= set(evidence_columns.keys())
    assert match_columns["evidence_id"].nullable
    assert {
        "lexical_score",
        "dense_score",
        "hybrid_score",
        "reranker_raw_score",
        "reranker_score",
        "final_match_score",
        "retrieval_model_versions_json",
    } <= set(match_columns.keys())
    assert aggregate_columns["application_id"].primary_key
    assert {"cv_file_id", "scoring_version", "model_versions_json", "explanation_json"} <= set(
        aggregate_columns.keys()
    )


def test_matching_foreign_keys_and_uniqueness_are_explicit() -> None:
    evidence_table = Base.metadata.tables["candidate_evidence"]
    match_table = Base.metadata.tables["requirement_matches"]
    aggregate_table = Base.metadata.tables["application_match_results"]
    embedding_table = Base.metadata.tables["embedding_records"]

    evidence_targets = {
        foreign_key.target_fullname
        for column in evidence_table.columns
        for foreign_key in column.foreign_keys
    }
    match_targets = {
        foreign_key.target_fullname
        for column in match_table.columns
        for foreign_key in column.foreign_keys
    }
    aggregate_targets = {
        foreign_key.target_fullname
        for column in aggregate_table.columns
        for foreign_key in column.foreign_keys
    }

    assert evidence_targets == {"users.id", "cv_files.id"}
    assert match_targets == {
        "applications.id",
        "vacancy_requirements.id",
        "candidate_evidence.id",
    }
    assert aggregate_targets == {"applications.id", "cv_files.id"}
    assert not any(column.foreign_keys for column in embedding_table.columns)
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"application_id", "requirement_id"}
        for constraint in match_table.constraints
    )
