from dataclasses import dataclass
from enum import StrEnum


class EffectiveValueSource(StrEnum):
    APPLICATION_OVERRIDE = "application_override"
    SITE_FIELD_OVERRIDE = "site_field_override"
    SITE_OVERRIDE = "site_override"
    RESUME = "resume"
    GLOBAL = "global"
    GENERATED = "generated"


class EffectiveValueNotFound(LookupError):
    pass


class EffectiveValueBlocked(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class EffectiveValueCandidate:
    value: str
    source_record_id: str | None = None
    is_sensitive: bool = False
    requires_review: bool = False

    def __post_init__(self) -> None:
        if type(self.value) is not str or not self.value:
            raise ValueError("Effective value candidate is invalid")


@dataclass(frozen=True, slots=True)
class ResolvedEffectiveValue:
    value: str
    source: EffectiveValueSource
    source_record_id: str | None
    is_sensitive: bool
    requires_review: bool


def resolve_effective_autofill_value(
    *,
    value_key: str,
    application_override: EffectiveValueCandidate | None = None,
    site_field_override: EffectiveValueCandidate | None = None,
    site_override: EffectiveValueCandidate | None = None,
    resume_value: EffectiveValueCandidate | None = None,
    global_value: EffectiveValueCandidate | None = None,
    generated_value: EffectiveValueCandidate | None = None,
    allow_sensitive: bool = False,
) -> ResolvedEffectiveValue:
    """Resolve one value deterministically without persistence or external access."""
    if type(value_key) is not str or not value_key.strip():
        raise ValueError("Effective value key is invalid")
    normalized_key = value_key.strip().casefold()
    if "password" in normalized_key or normalized_key.startswith("credentials."):
        raise EffectiveValueBlocked("Credential values cannot be resolved")
    if type(allow_sensitive) is not bool:
        raise ValueError("Sensitive-value permission is invalid")

    candidates = (
        (EffectiveValueSource.APPLICATION_OVERRIDE, application_override),
        (EffectiveValueSource.SITE_FIELD_OVERRIDE, site_field_override),
        (EffectiveValueSource.SITE_OVERRIDE, site_override),
        (EffectiveValueSource.RESUME, resume_value),
        (EffectiveValueSource.GLOBAL, global_value),
        (EffectiveValueSource.GENERATED, generated_value),
    )
    for source, candidate in candidates:
        if candidate is None:
            continue
        if candidate.is_sensitive and not allow_sensitive:
            raise EffectiveValueBlocked("Sensitive value requires explicit permission")
        return ResolvedEffectiveValue(
            value=candidate.value,
            source=source,
            source_record_id=candidate.source_record_id,
            is_sensitive=candidate.is_sensitive,
            requires_review=(
                candidate.requires_review
                or candidate.is_sensitive
                or source is EffectiveValueSource.GENERATED
            ),
        )
    raise EffectiveValueNotFound("No effective value is available")


def apply_effective_value_transformation(
    value: str,
    transformation: dict[str, object],
) -> str:
    """Apply one closed, deterministic text transformation."""
    if type(value) is not str or type(transformation) is not dict:
        raise ValueError("Effective value transformation is invalid")
    kind = transformation.get("kind", "identity")
    if kind == "identity":
        return value
    if kind == "trim":
        return value.strip()
    if kind == "lowercase":
        return value.lower()
    if kind == "uppercase":
        return value.upper()
    raise ValueError("Effective value transformation is invalid")
