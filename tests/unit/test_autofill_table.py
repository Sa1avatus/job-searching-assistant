from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint

from app.storage.database import Base
from app.storage.tables import AutofillValueRow  # noqa: F401


def test_autofill_values_table_has_exact_storage_contract() -> None:
    table = Base.metadata.tables["autofill_values"]

    assert list(table.columns.keys()) == [
        "id",
        "user_id",
        "key",
        "label",
        "value_type",
        "encrypted_value",
        "is_sensitive",
        "created_at",
        "updated_at",
    ]
    assert table.primary_key.columns.keys() == ["id"]
    assert all(not column.nullable for column in table.columns)


def test_autofill_values_columns_have_expected_types_and_lengths() -> None:
    columns = Base.metadata.tables["autofill_values"].columns

    assert isinstance(columns["id"].type, String)
    assert columns["id"].type.length == 36
    assert isinstance(columns["user_id"].type, String)
    assert columns["user_id"].type.length == 36
    assert isinstance(columns["key"].type, String)
    assert columns["key"].type.length == 200
    assert isinstance(columns["label"].type, String)
    assert columns["label"].type.length == 200
    assert isinstance(columns["value_type"].type, String)
    assert columns["value_type"].type.length == 30
    assert isinstance(columns["encrypted_value"].type, Text)
    assert isinstance(columns["is_sensitive"].type, Boolean)
    assert isinstance(columns["created_at"].type, DateTime)
    assert columns["created_at"].type.timezone is True
    assert isinstance(columns["updated_at"].type, DateTime)
    assert columns["updated_at"].type.timezone is True


def test_autofill_values_enforces_user_key_uniqueness() -> None:
    table = Base.metadata.tables["autofill_values"]
    matching_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_autofill_values_user_key"
    ]

    assert len(matching_constraints) == 1
    assert matching_constraints[0].columns.keys() == ["user_id", "key"]


def test_autofill_values_user_reference_cascades_and_is_indexed() -> None:
    table = Base.metadata.tables["autofill_values"]
    user_id = table.columns["user_id"]
    foreign_keys = tuple(user_id.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "users.id"
    assert foreign_keys[0].ondelete == "CASCADE"
    assert user_id.index is True
    assert {index.name for index in table.indexes} == {"ix_autofill_values_user_id"}


def test_autofill_values_has_expected_python_defaults() -> None:
    columns = Base.metadata.tables["autofill_values"].columns

    assert columns["id"].default is not None
    assert callable(columns["id"].default.arg)
    assert columns["is_sensitive"].default is not None
    assert columns["is_sensitive"].default.arg is False
    assert columns["created_at"].default is not None
    assert callable(columns["created_at"].default.arg)
    assert columns["updated_at"].default is not None
    assert callable(columns["updated_at"].default.arg)
    assert columns["updated_at"].onupdate is not None
    assert callable(columns["updated_at"].onupdate.arg)


def test_autofill_values_has_no_plaintext_or_credential_columns() -> None:
    column_names = set(Base.metadata.tables["autofill_values"].columns.keys())

    assert column_names.isdisjoint(
        {
            "value",
            "plaintext_value",
            "json_value",
            "password",
            "token",
            "one_time_code",
            "secret",
            "api_key",
            "credential",
        }
    )
