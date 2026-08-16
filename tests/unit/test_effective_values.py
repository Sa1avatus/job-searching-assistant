import pytest

from app.domain.effective_values import (
    EffectiveValueBlocked,
    EffectiveValueCandidate,
    EffectiveValueNotFound,
    EffectiveValueSource,
    apply_effective_value_transformation,
    resolve_effective_autofill_value,
)


@pytest.mark.parametrize(
    ("selected_argument", "expected_source"),
    [
        ("application_override", EffectiveValueSource.APPLICATION_OVERRIDE),
        ("site_field_override", EffectiveValueSource.SITE_FIELD_OVERRIDE),
        ("site_override", EffectiveValueSource.SITE_OVERRIDE),
        ("resume_value", EffectiveValueSource.RESUME),
        ("global_value", EffectiveValueSource.GLOBAL),
        ("generated_value", EffectiveValueSource.GENERATED),
    ],
)
def test_resolver_supports_every_precedence_level(
    selected_argument: str,
    expected_source: EffectiveValueSource,
) -> None:
    candidate = EffectiveValueCandidate("selected", source_record_id="record-1")

    result = resolve_effective_autofill_value(
        value_key="contact.email",
        **{selected_argument: candidate},
    )

    assert result.value == "selected"
    assert result.source is expected_source
    assert result.source_record_id == "record-1"
    assert result.requires_review is (expected_source is EffectiveValueSource.GENERATED)


def test_resolver_uses_first_candidate_in_precedence_order() -> None:
    result = resolve_effective_autofill_value(
        value_key="contact.email",
        application_override=EffectiveValueCandidate("application"),
        site_field_override=EffectiveValueCandidate("field"),
        site_override=EffectiveValueCandidate("site"),
        global_value=EffectiveValueCandidate("global"),
    )

    assert result.value == "application"
    assert result.source is EffectiveValueSource.APPLICATION_OVERRIDE


def test_resolver_reports_missing_value() -> None:
    with pytest.raises(EffectiveValueNotFound, match="^No effective value is available$"):
        resolve_effective_autofill_value(value_key="contact.email")


def test_resolver_blocks_sensitive_value_without_permission() -> None:
    with pytest.raises(
        EffectiveValueBlocked,
        match="^Sensitive value requires explicit permission$",
    ):
        resolve_effective_autofill_value(
            value_key="legal.work_authorization",
            site_override=EffectiveValueCandidate("yes", is_sensitive=True),
        )


def test_resolver_marks_permitted_sensitive_value_for_review() -> None:
    result = resolve_effective_autofill_value(
        value_key="legal.work_authorization",
        site_override=EffectiveValueCandidate("yes", is_sensitive=True),
        allow_sensitive=True,
    )

    assert result.is_sensitive is True
    assert result.requires_review is True


@pytest.mark.parametrize("value_key", ["credentials.password", "custom.password_hint"])
def test_resolver_never_returns_credentials(value_key: str) -> None:
    with pytest.raises(EffectiveValueBlocked, match="^Credential values cannot be resolved$"):
        resolve_effective_autofill_value(
            value_key=value_key,
            global_value=EffectiveValueCandidate("secret", is_sensitive=True),
            allow_sensitive=True,
        )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("identity", " Value "),
        ("trim", "Value"),
        ("lowercase", " value "),
        ("uppercase", " VALUE "),
    ],
)
def test_effective_value_transformations_are_closed(kind: str, expected: str) -> None:
    assert apply_effective_value_transformation(" Value ", {"kind": kind}) == expected


def test_effective_value_transformation_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="^Effective value transformation is invalid$"):
        apply_effective_value_transformation("value", {"kind": "python"})
