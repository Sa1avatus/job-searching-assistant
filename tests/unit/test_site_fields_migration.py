from pathlib import Path


def test_site_fields_migration_is_linear_and_complete() -> None:
    source = Path("migrations/versions/0023_site_fields_mappings_overrides.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0023"' in source
    assert 'down_revision = "0022"' in source
    for table_name in ("site_fields", "site_field_mappings", "site_value_overrides"):
        assert f'op.create_table(\n        "{table_name}"' in source
        assert f'op.drop_table("{table_name}")' in source
    assert "uq_site_fields_definition_field_key" in source
    assert "uq_site_field_mappings_site_field_id" in source
    assert "uq_site_value_overrides_scope_value_key" in source
