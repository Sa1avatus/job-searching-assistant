from sqlalchemy import JSON, Boolean, String, Text, UniqueConstraint

from app.storage.tables import SiteFieldMappingRow, SiteFieldRow, SiteValueOverrideRow


def test_site_field_table_has_discovery_metadata_and_unique_field_key() -> None:
    table = SiteFieldRow.__table__
    assert set(table.columns.keys()) == {
        "id",
        "site_definition_id",
        "field_key",
        "semantic_key",
        "label",
        "field_type",
        "is_required",
        "options",
        "selector_candidates",
        "created_at",
        "updated_at",
    }
    assert isinstance(table.c.field_key.type, String)
    assert isinstance(table.c.options.type, JSON)
    assert isinstance(table.c.selector_candidates.type, JSON)
    constraints = [item for item in table.constraints if isinstance(item, UniqueConstraint)]
    assert any(
        item.name == "uq_site_fields_definition_field_key"
        and item.columns.keys() == ["site_definition_id", "field_key"]
        for item in constraints
    )


def test_site_field_mapping_is_one_to_one_and_typed() -> None:
    table = SiteFieldMappingRow.__table__
    assert isinstance(table.c.transformation.type, JSON)
    assert isinstance(table.c.review_required.type, Boolean)
    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == "uq_site_field_mappings_site_field_id"
        for item in table.constraints
    )


def test_site_value_override_is_encrypted_and_scope_unique() -> None:
    table = SiteValueOverrideRow.__table__
    assert isinstance(table.c.encrypted_value.type, Text)
    assert isinstance(table.c.is_sensitive.type, Boolean)
    assert table.c.site_field_id.nullable is True
    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == "uq_site_value_overrides_scope_value_key"
        and item.columns.keys() == ["site_definition_id", "scope_key", "value_key"]
        for item in table.constraints
    )
