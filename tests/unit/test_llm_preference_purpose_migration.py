from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0035_llm_preference_purpose.py"
)


def test_llm_preference_purpose_migration_revision_chain() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    assert assignments["revision"] == "0035"
    assert assignments["down_revision"] == "0034"


def test_llm_preference_purpose_migration_adds_purpose_column() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert '"purpose"' in source
    assert 'server_default="materials"' in source


def test_llm_preference_purpose_migration_replaces_primary_key() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'drop_constraint("llm_preferences_pkey"' in source
    assert 'create_primary_key("pk_llm_preferences"' in source


def test_llm_preference_purpose_migration_downgrade_restores_primary_key() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'create_primary_key("llm_preferences_pkey"' in source
    assert 'drop_column("llm_preferences", "purpose")' in source
