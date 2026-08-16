from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0027_component_scoring_columns.py"
)


def test_component_scoring_migration_revision_chain() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    assert assignments["revision"] == "0027"
    assert assignments["down_revision"] == "0026"


def test_component_scoring_migration_adds_four_columns() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert '"language_score"' in source
    assert '"semantic_similarity"' in source
    assert '"reranker_score"' in source
    assert '"requirements_match"' in source


def test_component_scoring_migration_has_check_constraints() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ck_application_match_results_language_range" in source
    assert "ck_application_match_results_semantic_range" in source
    assert "ck_application_match_results_reranker_range" in source
    assert "ck_application_match_results_requirements_range" in source


def test_component_scoring_migration_downgrade_removes_all() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'op.drop_column("application_match_results", "requirements_match")' in source
    assert 'op.drop_column("application_match_results", "reranker_score")' in source
    assert 'op.drop_column("application_match_results", "semantic_similarity")' in source
    assert 'op.drop_column("application_match_results", "language_score")' in source
