import re

_FIXED_AUTOFILL_KEYS: frozenset[str] = frozenset(
    {
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
    }
)
_FORBIDDEN_CREDENTIAL_SEGMENTS: frozenset[str] = frozenset(
    {
        "password",
        "passcode",
        "otp",
        "one_time_code",
        "verification_code",
        "secret",
        "token",
        "api_key",
    }
)
_CUSTOM_AUTOFILL_KEY_PATTERN = re.compile(r"custom\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")


class InvalidAutofillKey(ValueError):
    pass


def validate_autofill_key(key: str) -> str:
    if not isinstance(key, str):
        raise InvalidAutofillKey("Autofill key must be text")
    if not key or len(key) > 200:
        raise InvalidAutofillKey("Autofill key is invalid")
    if key in _FIXED_AUTOFILL_KEYS:
        return key
    if _CUSTOM_AUTOFILL_KEY_PATTERN.fullmatch(key) is None:
        raise InvalidAutofillKey("Autofill key is invalid")
    if any(segment in _FORBIDDEN_CREDENTIAL_SEGMENTS for segment in key.split(".")[1:]):
        raise InvalidAutofillKey("Credential autofill keys are forbidden")
    return key
