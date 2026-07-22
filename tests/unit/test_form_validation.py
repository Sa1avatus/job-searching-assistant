from app.domain.forms import (
    FieldConstraints,
    FormField,
    FormFieldType,
    validate_field_answer,
)


def field(
    field_type: FormFieldType,
    *,
    required: bool = False,
    options: tuple[str, ...] = (),
    constraints: FieldConstraints | None = None,
) -> FormField:
    return FormField(
        field_id="controlled",
        label="Controlled field",
        field_type=field_type,
        is_required=required,
        options=options,
        constraints=constraints or FieldConstraints(),
    )


def test_validation_enforces_required_boolean_and_allowed_options() -> None:
    assert validate_field_answer(field(FormFieldType.TEXT, required=True), "") == (
        "required value is missing",
    )
    assert validate_field_answer(field(FormFieldType.CHECKBOX), "yes") == (
        "checkbox answer must be boolean",
    )
    assert validate_field_answer(field(FormFieldType.SELECT, options=("Yes", "No")), "Maybe") == (
        "answer is not one of the allowed options",
    )


def test_validation_enforces_text_number_date_and_file_constraints() -> None:
    text_field = field(
        FormFieldType.TEXT,
        constraints=FieldConstraints(min_length=3, max_length=5, pattern="[A-Z]+"),
    )
    assert validate_field_answer(text_field, "ab") == (
        "answer is shorter than the minimum length",
        "answer does not match the required pattern",
    )
    number_field = field(
        FormFieldType.NUMBER,
        constraints=FieldConstraints(minimum="1", maximum="5", step="0.5"),
    )
    assert validate_field_answer(number_field, "5.1") == (
        "answer exceeds the maximum",
        "answer does not satisfy the required step",
    )
    date_field = field(
        FormFieldType.DATE,
        constraints=FieldConstraints(minimum="2026-01-01", maximum="2026-12-31"),
    )
    assert validate_field_answer(date_field, "2025-12-31") == ("date is before the minimum",)
    file_field = field(
        FormFieldType.FILE,
        constraints=FieldConstraints(accepted_file_types=(".pdf", ".docx")),
    )
    assert validate_field_answer(file_field, "resume.exe") == ("file extension is not accepted",)


def test_validation_accepts_valid_typed_answers() -> None:
    assert validate_field_answer(field(FormFieldType.CHECKBOX, required=True), True) == ()
    assert (
        validate_field_answer(field(FormFieldType.RADIO, options=("Remote", "Hybrid")), "Remote")
        == ()
    )
    assert (
        validate_field_answer(
            field(FormFieldType.NUMBER, constraints=FieldConstraints(minimum="0", step="0.5")),
            "2.5",
        )
        == ()
    )
