from __future__ import annotations

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint

from app.storage.database import Base
from app.storage.tables import SiteDefinitionRow  # noqa: F401


def test_site_definitions_table_has_exact_storage_contract() -> None:
    table = Base.metadata.tables["site_definitions"]

    assert list(table.columns.keys()) == [
        "id",
        "user_id",
        "site_key",
        "name",
        "login_url",
        "allowed_hosts",
        "authorization_rules",
        "archived_at",
        "created_at",
        "updated_at",
    ]
    assert table.primary_key.columns.keys() == ["id"]
    assert table.columns["archived_at"].nullable is True
    assert all(not column.nullable for column in table.columns if column.name != "archived_at")


def test_site_definitions_columns_have_expected_types() -> None:
    columns = Base.metadata.tables["site_definitions"].columns

    assert isinstance(columns["id"].type, String)
    assert columns["id"].type.length == 36
    assert isinstance(columns["user_id"].type, String)
    assert columns["user_id"].type.length == 36
    assert isinstance(columns["site_key"].type, String)
    assert columns["site_key"].type.length == 100
    assert isinstance(columns["name"].type, String)
    assert columns["name"].type.length == 200
    assert isinstance(columns["login_url"].type, Text)
    assert isinstance(columns["allowed_hosts"].type, JSON)
    assert isinstance(columns["authorization_rules"].type, JSON)
    for name in ("archived_at", "created_at", "updated_at"):
        assert isinstance(columns[name].type, DateTime)
        assert columns[name].type.timezone is True


def test_site_definitions_enforces_user_site_key_uniqueness() -> None:
    table = Base.metadata.tables["site_definitions"]
    constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_site_definitions_user_site_key"
    ]

    assert len(constraints) == 1
    assert constraints[0].columns.keys() == ["user_id", "site_key"]


def test_site_definitions_user_reference_cascades_and_is_indexed() -> None:
    table = Base.metadata.tables["site_definitions"]
    user_id = table.columns["user_id"]
    foreign_keys = tuple(user_id.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "users.id"
    assert foreign_keys[0].ondelete == "CASCADE"
    assert user_id.index is True
    assert {index.name for index in table.indexes} == {"ix_site_definitions_user_id"}


def test_site_definitions_has_expected_python_defaults() -> None:
    columns = Base.metadata.tables["site_definitions"].columns

    for name in ("id", "allowed_hosts", "authorization_rules", "created_at", "updated_at"):
        assert columns[name].default is not None
        assert callable(columns[name].default.arg)
    assert columns["updated_at"].onupdate is not None
    assert callable(columns["updated_at"].onupdate.arg)


def test_site_definitions_excludes_credentials_and_workflows() -> None:
    column_names = set(Base.metadata.tables["site_definitions"].columns.keys())

    assert column_names.isdisjoint(
        {
            "password",
            "token",
            "api_key",
            "cookies",
            "browser_state",
            "workflow",
            "workflow_steps",
            "javascript",
        }
    )
