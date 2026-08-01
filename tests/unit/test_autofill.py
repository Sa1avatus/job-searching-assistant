import pytest

from app.domain.autofill import AutofillValueType


def test_autofill_value_type_has_exact_ordered_members() -> None:
    assert [(member.name, member.value) for member in AutofillValueType] == [
        ("TEXT", "text"),
        ("EMAIL", "email"),
        ("PHONE", "phone"),
        ("URL", "url"),
        ("DECIMAL", "decimal"),
        ("CURRENCY", "currency"),
        ("DURATION", "duration"),
        ("BOOLEAN", "boolean"),
    ]


def test_autofill_value_type_serializes_every_member_as_string() -> None:
    for member in AutofillValueType:
        assert str(member) == member.value


def test_autofill_value_type_constructs_every_supported_value() -> None:
    for member in AutofillValueType:
        assert AutofillValueType(member.value) is member


@pytest.mark.parametrize("unsupported_value", ["unknown", "TEXT", "", " text "])
def test_autofill_value_type_rejects_non_exact_value(unsupported_value: str) -> None:
    with pytest.raises(ValueError):
        AutofillValueType(unsupported_value)
