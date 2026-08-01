from enum import StrEnum


class AutofillValueType(StrEnum):
    """Supported serialized representations for canonical autofill values."""

    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    DURATION = "duration"
    BOOLEAN = "boolean"
