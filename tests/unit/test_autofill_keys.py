import pytest

from app.domain.autofill_keys import InvalidAutofillKey, validate_autofill_key

FIXED_AUTOFILL_KEYS = (
    "identity.full_name",
    "identity.first_name",
    "identity.last_name",
    "contact.email",
    "contact.phone",
    "contact.linkedin_url",
    "location.country",
    "location.city",
    "job_preferences.expected_salary",
    "job_preferences.currency",
    "job_preferences.notice_period",
    "job_preferences.relocation_ready",
)
FORBIDDEN_CREDENTIAL_SEGMENTS = (
    "password",
    "passcode",
    "otp",
    "one_time_code",
    "verification_code",
    "secret",
    "token",
    "api_key",
)


@pytest.mark.parametrize("key", FIXED_AUTOFILL_KEYS)
def test_validate_autofill_key_accepts_fixed_key_unchanged(key: str) -> None:
    assert validate_autofill_key(key) is key


@pytest.mark.parametrize(
    "key",
    [
        "custom.a",
        "custom.field_name",
        "custom.section.field1",
    ],
)
def test_validate_autofill_key_accepts_custom_key_unchanged(key: str) -> None:
    assert validate_autofill_key(key) is key


def test_validate_autofill_key_accepts_200_character_boundary() -> None:
    key = f"custom.{'a' * 193}"

    assert len(key) == 200
    assert validate_autofill_key(key) is key


def test_validate_autofill_key_rejects_non_text_without_disclosure() -> None:
    with pytest.raises(InvalidAutofillKey) as error:
        validate_autofill_key(123)  # type: ignore[arg-type]

    assert str(error.value) == "Autofill key must be text"
    assert "123" not in str(error.value)


@pytest.mark.parametrize(
    "key",
    [
        "",
        f"custom.{'a' * 194}",
        "CUSTOM.FIELD",
        " custom.field",
        "custom.field ",
        "custom",
        "custom.",
        "custom..field",
        "custom.field-name",
        "custom.1field",
        "custom._field",
        "custom.а",
    ],
)
def test_validate_autofill_key_rejects_invalid_key_without_disclosure(key: str) -> None:
    with pytest.raises(InvalidAutofillKey) as error:
        validate_autofill_key(key)

    assert str(error.value) == "Autofill key is invalid"
    if key:
        assert key not in str(error.value)


@pytest.mark.parametrize("segment", FORBIDDEN_CREDENTIAL_SEGMENTS)
@pytest.mark.parametrize("template", ["custom.{segment}", "custom.section.{segment}"])
def test_validate_autofill_key_rejects_credential_segment(
    segment: str,
    template: str,
) -> None:
    key = template.format(segment=segment)

    with pytest.raises(InvalidAutofillKey) as error:
        validate_autofill_key(key)

    assert str(error.value) == "Credential autofill keys are forbidden"
    assert key not in str(error.value)
