from app.browser.form_discovery import detect_form_changes, form_fingerprint
from app.domain.forms import FormField, FormFieldType


def _field(
    field_id: str,
    *,
    field_type: FormFieldType = FormFieldType.TEXT,
    label: str = "",
    is_required: bool = False,
    options: tuple[str, ...] = (),
) -> FormField:
    return FormField(
        field_id=field_id,
        label=label,
        field_type=field_type,
        is_required=is_required,
        options=options,
    )


def test_fingerprint_is_stable_for_same_fields() -> None:
    fields = (
        _field("email", label="Email", is_required=True),
        _field("name", label="Full Name"),
    )
    assert form_fingerprint(fields) == form_fingerprint(fields)


def test_fingerprint_changes_when_fields_change() -> None:
    old = (_field("email", label="Email"),)
    new = (_field("email", label="Email Address"),)
    assert form_fingerprint(old) != form_fingerprint(new)


def test_fingerprint_order_independent() -> None:
    a = (_field("x"), _field("y"))
    b = (_field("y"), _field("x"))
    assert form_fingerprint(a) == form_fingerprint(b)


def test_detect_form_changes_stable() -> None:
    fields = (_field("email"), _field("name"))
    result = detect_form_changes(fields, fields)
    assert result["stable"] is True
    assert result["added"] == []
    assert result["removed"] == []
    assert result["changed"] == []


def test_detect_form_changes_added_field() -> None:
    old = (_field("email"),)
    new = (_field("email"), _field("phone"))
    result = detect_form_changes(old, new)
    assert result["stable"] is False
    assert result["added"] == ["phone"]


def test_detect_form_changes_removed_field() -> None:
    old = (_field("email"), _field("phone"))
    new = (_field("email"),)
    result = detect_form_changes(old, new)
    assert result["removed"] == ["phone"]


def test_detect_form_changes_type_changed() -> None:
    old = (_field("bio", field_type=FormFieldType.TEXT),)
    new = (_field("bio", field_type=FormFieldType.TEXTAREA),)
    result = detect_form_changes(old, new)
    assert result["changed"] == ["bio"]


def test_detect_form_changes_required_changed() -> None:
    old = (_field("email", is_required=False),)
    new = (_field("email", is_required=True),)
    result = detect_form_changes(old, new)
    assert result["changed"] == ["email"]
