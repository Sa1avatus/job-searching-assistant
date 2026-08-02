from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.autofill_keys import validate_autofill_key
from app.domain.forms import FormField
from app.security.autofill_encryption import encrypt_autofill_value
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.storage.tables import (
    SiteDefinitionRow,
    SiteFieldMappingRow,
    SiteFieldRow,
    SiteValueOverrideRow,
    UserRow,
)


class InvalidSiteField(ValueError):
    pass


def save_discovered_site_fields(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
    fields: tuple[FormField, ...],
) -> tuple[SiteFieldRow, ...]:
    """Upsert deterministic discovery results without deleting existing mappings."""
    site = _owned_active_site(session, user_id=user_id, site_definition_id=site_definition_id)
    if type(fields) is not tuple or any(not isinstance(field, FormField) for field in fields):
        raise InvalidSiteField("Discovered fields are invalid")
    existing_rows = {
        row.field_key: row
        for row in session.scalars(
            select(SiteFieldRow).where(SiteFieldRow.site_definition_id == site.id)
        )
    }
    saved: list[SiteFieldRow] = []
    seen: set[str] = set()
    for field in fields:
        field_key = field.field_id.strip()
        if not field_key or len(field_key) > 200 or field_key in seen:
            raise InvalidSiteField("Discovered field key is invalid")
        seen.add(field_key)
        candidates = field.locator_candidates or (
            (field.source_locator,) if field.source_locator else ()
        )
        selector_candidates = [_selector_payload(candidate) for candidate in candidates]
        row = existing_rows.get(field_key) or SiteFieldRow(
            site_definition_id=site.id,
            field_key=field_key,
        )
        row.semantic_key = field.semantic_category[:200] or "custom"
        row.label = field.label[:300]
        row.field_type = field.field_type.value
        row.is_required = field.is_required
        row.options = list(field.options)
        row.selector_candidates = selector_candidates
        session.add(row)
        saved.append(row)
    session.commit()
    return tuple(saved)


def upsert_site_field_mapping(
    session: Session,
    *,
    user_id: str,
    site_field_id: str,
    value_key: str,
    transformation: dict[str, object] | None = None,
    review_required: bool = False,
) -> SiteFieldMappingRow:
    field = _owned_site_field(session, user_id=user_id, site_field_id=site_field_id)
    validated_key = validate_autofill_key(value_key)
    if type(review_required) is not bool:
        raise InvalidSiteField("Mapping review flag is invalid")
    normalized_transformation = _validate_transformation(transformation or {})
    row = session.scalar(
        select(SiteFieldMappingRow).where(SiteFieldMappingRow.site_field_id == field.id)
    ) or SiteFieldMappingRow(site_field_id=field.id)
    row.value_key = validated_key
    row.transformation = normalized_transformation
    row.review_required = review_required
    session.add(row)
    session.commit()
    return row


def upsert_site_value_override(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
    value_key: str,
    serialized_value: str,
    is_sensitive: bool,
    encryption_key: str,
    site_field_id: str | None = None,
) -> SiteValueOverrideRow:
    site = _owned_active_site(session, user_id=user_id, site_definition_id=site_definition_id)
    validated_key = validate_autofill_key(value_key)
    if type(is_sensitive) is not bool:
        raise InvalidSiteField("Override sensitivity flag is invalid")
    field = None
    if site_field_id is not None:
        field = _owned_site_field(session, user_id=user_id, site_field_id=site_field_id)
        if field.site_definition_id != site.id:
            raise EntityNotFoundError("Site field not found")
    scope_key = field.id if field is not None else "*"
    row = session.scalar(
        select(SiteValueOverrideRow).where(
            SiteValueOverrideRow.site_definition_id == site.id,
            SiteValueOverrideRow.scope_key == scope_key,
            SiteValueOverrideRow.value_key == validated_key,
        )
    ) or SiteValueOverrideRow(
        site_definition_id=site.id,
        site_field_id=field.id if field is not None else None,
        scope_key=scope_key,
        value_key=validated_key,
    )
    row.encrypted_value = encrypt_autofill_value(
        serialized_value,
        encryption_key=encryption_key,
    )
    row.is_sensitive = is_sensitive
    session.add(row)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise DuplicateEntityError("Site override already exists") from error
    return row


def _owned_active_site(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
) -> SiteDefinitionRow:
    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")
    site = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if site is None:
        raise EntityNotFoundError("Site definition not found")
    return site


def _owned_site_field(
    session: Session,
    *,
    user_id: str,
    site_field_id: str,
) -> SiteFieldRow:
    field = session.scalar(
        select(SiteFieldRow)
        .join(SiteDefinitionRow, SiteDefinitionRow.id == SiteFieldRow.site_definition_id)
        .where(
            SiteFieldRow.id == site_field_id,
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if field is None:
        raise EntityNotFoundError("Site field not found")
    return field


def _selector_payload(candidate: str) -> dict[str, str]:
    if type(candidate) is not str or ":" not in candidate or len(candidate) > 1_000:
        raise InvalidSiteField("Selector candidate is invalid")
    kind, value = candidate.split(":", 1)
    if kind not in {"label", "placeholder", "id", "name", "nth"} or not value:
        raise InvalidSiteField("Selector candidate is invalid")
    return {"kind": kind, "value": value}


def _validate_transformation(value: dict[str, object]) -> dict[str, object]:
    if type(value) is not dict:
        raise InvalidSiteField("Mapping transformation is invalid")
    if not value:
        return {}
    if set(value) != {"kind"} or value["kind"] not in {
        "identity",
        "trim",
        "lowercase",
        "uppercase",
    }:
        raise InvalidSiteField("Mapping transformation is invalid")
    return {"kind": value["kind"]}
