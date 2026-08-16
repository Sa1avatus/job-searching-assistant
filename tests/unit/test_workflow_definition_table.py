from sqlalchemy import JSON, CheckConstraint, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import Session

from app.storage.database import Base
from app.storage.tables import SiteDefinitionRow, UserRow, WorkflowDefinitionRow


def test_workflow_definition_row_table() -> None:
    assert WorkflowDefinitionRow.__tablename__ == "workflow_definitions"
    columns = set(WorkflowDefinitionRow.__table__.columns.keys())
    expected_columns = {
        "id",
        "site_definition_id",
        "version",
        "status",
        "vacancy_url_patterns",
        "created_at",
        "updated_at",
    }
    assert columns == expected_columns

    id_column = WorkflowDefinitionRow.__table__.c.id
    site_definition_id_column = WorkflowDefinitionRow.__table__.c.site_definition_id
    version_column = WorkflowDefinitionRow.__table__.c.version
    status_column = WorkflowDefinitionRow.__table__.c.status
    vacancy_url_patterns_column = WorkflowDefinitionRow.__table__.c.vacancy_url_patterns

    assert isinstance(id_column.type, String)
    assert id_column.primary_key is True
    assert id_column.nullable is False
    assert id_column.default is not None

    assert isinstance(site_definition_id_column.type, String)
    assert site_definition_id_column.nullable is False
    assert site_definition_id_column.index is True
    fk = next(iter(site_definition_id_column.foreign_keys))
    assert fk.target_fullname == "site_definitions.id"
    assert fk.ondelete == "CASCADE"

    assert isinstance(version_column.type, Integer)
    assert version_column.nullable is False
    assert isinstance(status_column.type, String)
    assert status_column.nullable is False
    assert status_column.index is True
    assert status_column.default is not None and status_column.default.arg == "draft"
    assert isinstance(vacancy_url_patterns_column.type, JSON)
    assert vacancy_url_patterns_column.nullable is False
    assert vacancy_url_patterns_column.default is not None and (
        isinstance(vacancy_url_patterns_column.default.arg, list)
        or callable(vacancy_url_patterns_column.default.arg)
    )

    unique_constraints = [
        item
        for item in WorkflowDefinitionRow.__table__.constraints
        if isinstance(item, UniqueConstraint)
    ]
    assert any(
        item.name == "uq_workflow_definitions_site_version"
        and set(item.columns.keys()) == {"site_definition_id", "version"}
        for item in unique_constraints
    )

    check_constraints = [
        item
        for item in WorkflowDefinitionRow.__table__.constraints
        if isinstance(item, CheckConstraint)
    ]
    assert any(
        item.name == "ck_workflow_definitions_status"
        and "draft" in item.sqltext.text
        and "testing" in item.sqltext.text
        and "active" in item.sqltext.text
        and "broken" in item.sqltext.text
        and "archived" in item.sqltext.text
        for item in check_constraints
    )

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user_row = UserRow(id="user-1", display_name="Candidate")
            site_definition_row = SiteDefinitionRow(
                id="site-1",
                user_id="user-1",
                site_key="careers",
                name="Careers",
                login_url="https://careers.example.test/login",
                allowed_hosts=["careers.example.test"],
                authorization_rules={},
            )
            session.add(user_row)
            session.add(site_definition_row)
            workflow_definition_row = WorkflowDefinitionRow(site_definition_id="site-1", version=1)
            session.add(workflow_definition_row)
            session.commit()

            assert workflow_definition_row.id is not None
            assert workflow_definition_row.status == "draft"
            assert workflow_definition_row.vacancy_url_patterns == []
    finally:
        engine.dispose()
