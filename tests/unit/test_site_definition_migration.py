from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0022_site_definitions.py"
)


def test_site_definition_migration_revision_and_operations() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    assert assignments["revision"] == "0022"
    assert assignments["down_revision"] == "0021"
    assert 'op.create_table(\n        "site_definitions"' in source
    assert 'name="uq_site_definitions_user_site_key"' in source
    assert '"ix_site_definitions_user_id"' in source
    assert "users.id" in source
    assert "ondelete=\"CASCADE\"" in source


def test_site_definition_migration_downgrade_order() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    drop_index = source.index(
        'op.drop_index("ix_site_definitions_user_id", table_name="site_definitions")'
    )
    drop_table = source.index('op.drop_table("site_definitions")')

    assert drop_index < drop_table
