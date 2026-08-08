from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0024_workflow_definitions_and_steps.py"
)


def _operation_order(function_name: str) -> list[tuple[str, str]]:
    module = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    operations: list[tuple[str, str]] = []
    for statement in function.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
            continue
        if call.func.value.id != "op" or not call.args:
            continue
        target = ast.literal_eval(call.args[0])
        assert isinstance(target, str)
        operations.append((call.func.attr, target))
    return operations


def test_workflow_persistence_migration_operation_order() -> None:
    assert _operation_order("upgrade") == [
        ("create_table", "workflow_definitions"),
        ("create_index", "ix_workflow_definitions_site_definition_id"),
        ("create_index", "ix_workflow_definitions_status"),
        ("create_table", "workflow_steps"),
        ("create_index", "ix_workflow_steps_workflow_definition_id"),
        ("create_index", "ix_workflow_steps_action_type"),
    ]

    assert _operation_order("downgrade") == [
        ("drop_index", "ix_workflow_steps_action_type"),
        ("drop_index", "ix_workflow_steps_workflow_definition_id"),
        ("drop_table", "workflow_steps"),
        ("drop_index", "ix_workflow_definitions_status"),
        ("drop_index", "ix_workflow_definitions_site_definition_id"),
        ("drop_table", "workflow_definitions"),
    ]
