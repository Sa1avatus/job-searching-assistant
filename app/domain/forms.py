import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path


class FormFieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    DATE = "date"
    NUMBER = "number"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FieldConstraints:
    min_length: int | None = None
    max_length: int | None = None
    minimum: str | None = None
    maximum: str | None = None
    step: str | None = None
    pattern: str | None = None
    accepted_file_types: tuple[str, ...] = ()
    allows_multiple: bool = False


@dataclass(frozen=True, slots=True)
class FormField:
    field_id: str
    label: str
    field_type: FormFieldType
    is_required: bool
    options: tuple[str, ...] = ()
    current_value: str | None = None
    semantic_category: str = "custom"
    confidence: float = 0.0
    source_locator: str = ""
    constraints: FieldConstraints = FieldConstraints()

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.field_id:
            raise ValueError("field_id is required")


def validate_field_answer(field: FormField, answer: str | bool | None) -> tuple[str, ...]:
    errors: list[str] = []
    normalized = answer.strip() if isinstance(answer, str) else answer
    if field.is_required and (normalized is None or normalized == "" or normalized is False):
        errors.append("required value is missing")
        return tuple(errors)
    if normalized is None or normalized == "":
        return ()
    if field.field_type is FormFieldType.CHECKBOX:
        if not isinstance(normalized, bool):
            errors.append("checkbox answer must be boolean")
        return tuple(errors)
    if not isinstance(normalized, str):
        return ("answer must be text",)
    if (
        field.field_type in {FormFieldType.SELECT, FormFieldType.RADIO}
        and field.options
        and normalized not in field.options
    ):
        errors.append("answer is not one of the allowed options")
    if field.field_type in {FormFieldType.TEXT, FormFieldType.TEXTAREA}:
        constraints = field.constraints
        if constraints.min_length is not None and len(normalized) < constraints.min_length:
            errors.append("answer is shorter than the minimum length")
        if constraints.max_length is not None and len(normalized) > constraints.max_length:
            errors.append("answer exceeds the maximum length")
        if constraints.pattern:
            try:
                if re.fullmatch(constraints.pattern, normalized) is None:
                    errors.append("answer does not match the required pattern")
            except re.error:
                errors.append("field pattern is invalid")
    elif field.field_type is FormFieldType.NUMBER:
        errors.extend(_validate_number(field.constraints, normalized))
    elif field.field_type is FormFieldType.DATE:
        errors.extend(_validate_date(field.constraints, normalized))
    elif field.field_type is FormFieldType.FILE:
        allowed_extensions = tuple(
            file_type.casefold()
            for file_type in field.constraints.accepted_file_types
            if file_type.startswith(".")
        )
        if allowed_extensions and Path(normalized).suffix.casefold() not in allowed_extensions:
            errors.append("file extension is not accepted")
    return tuple(errors)


def _validate_number(constraints: FieldConstraints, answer: str) -> list[str]:
    try:
        number = Decimal(answer)
    except InvalidOperation:
        return ["answer is not a valid number"]
    errors: list[str] = []
    minimum_text = constraints.minimum
    maximum_text = constraints.maximum
    try:
        minimum = Decimal(minimum_text) if minimum_text is not None else None
        maximum = Decimal(maximum_text) if maximum_text is not None else None
    except InvalidOperation:
        return ["field numeric constraint is invalid"]
    if minimum is not None and number < minimum:
        errors.append("answer is below the minimum")
    if maximum is not None and number > maximum:
        errors.append("answer exceeds the maximum")
    step_text = constraints.step
    if step_text is not None and step_text != "any":
        try:
            step = Decimal(step_text)
            if step > 0 and (number - (minimum or Decimal(0))) % step != 0:
                errors.append("answer does not satisfy the required step")
        except InvalidOperation:
            errors.append("field step is invalid")
    return errors


def _validate_date(constraints: FieldConstraints, answer: str) -> list[str]:
    try:
        selected_date = date.fromisoformat(answer)
    except ValueError:
        return ["answer is not a valid ISO date"]
    errors: list[str] = []
    try:
        if constraints.minimum and selected_date < date.fromisoformat(constraints.minimum):
            errors.append("date is before the minimum")
        if constraints.maximum and selected_date > date.fromisoformat(constraints.maximum):
            errors.append("date is after the maximum")
    except ValueError:
        errors.append("field date constraint is invalid")
    return errors
