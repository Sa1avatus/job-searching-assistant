from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, String, UniqueConstraint

from app.storage.tables import WorkflowStepRow


def test_workflow_step_row_contract() -> None:
    table = WorkflowStepRow.__table__

    assert table.name == "workflow_steps"
    assert set(table.columns.keys()) == {
        "id",
        "workflow_definition_id",
        "position",
        "action_type",
        "selector_candidates",
        "condition",
        "parameters",
        "timeout_ms",
        "is_enabled",
        "created_at",
        "updated_at",
    }
    assert isinstance(table.c.id.type, String)
    assert table.c.id.type.length == 36
    assert table.c.id.primary_key is True
    assert isinstance(table.c.position.type, Integer)
    assert isinstance(table.c.action_type.type, String)
    assert table.c.action_type.type.length == 30
    assert table.c.workflow_definition_id.index is True
    foreign_key = next(iter(table.c.workflow_definition_id.foreign_keys))
    assert foreign_key.target_fullname == "workflow_definitions.id"
    assert foreign_key.ondelete == "CASCADE"
    assert isinstance(table.c.selector_candidates.type, JSON)
    assert isinstance(table.c.condition.type, JSON)
    assert isinstance(table.c.parameters.type, JSON)
    assert isinstance(table.c.timeout_ms.type, Integer)
    assert table.c.timeout_ms.default is not None
    assert table.c.timeout_ms.default.arg == 10_000
    assert isinstance(table.c.is_enabled.type, Boolean)
    assert table.c.is_enabled.default is not None
    assert table.c.is_enabled.default.arg is True

    unique_constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_workflow_steps_definition_position"
    )
    assert set(unique_constraint.columns.keys()) == {"workflow_definition_id", "position"}

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert set(checks) == {
        "ck_workflow_steps_action_type",
        "ck_workflow_steps_timeout_range",
    }
    for action_type in (
        "navigate",
        "fill",
        "upload",
        "select",
        "check",
        "click",
        "wait",
        "assert",
        "human_review",
        "submit",
    ):
        assert action_type in checks["ck_workflow_steps_action_type"]
    assert checks["ck_workflow_steps_timeout_range"] == ("timeout_ms >= 1 AND timeout_ms <= 120000")
