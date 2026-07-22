import asyncio
from pathlib import Path

from app.browser.engine import PlaywrightEngine
from app.browser.form_discovery import discover_form_fields
from app.domain.forms import FormFieldType


def test_discovery_returns_typed_accessible_fields(tmp_path: Path) -> None:
    async def run_discovery() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            page = await engine.new_page()
            navigation = await engine.navigate(page, fixture_path.resolve().as_uri())
            fields = await discover_form_fields(page)

        assert navigation.is_successful
        fields_by_id = {field.field_id: field for field in fields}
        assert fields_by_id["email"].semantic_category == "email"
        assert fields_by_id["resume"].field_type is FormFieldType.FILE
        assert fields_by_id["authorization"].semantic_category == "work_authorization"
        assert fields_by_id["authorization"].options == ("Select an option", "Yes", "No")
        assert all(field.source_locator for field in fields)

    asyncio.run(run_discovery())


def test_discovery_covers_field_types_groups_and_constraints(tmp_path: Path) -> None:
    async def run_discovery() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "form_fields.html"
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            page = await engine.new_page()
            assert (await engine.navigate(page, fixture_path.resolve().as_uri())).is_successful
            fields = await discover_form_fields(page)

        fields_by_id = {field.field_id: field for field in fields}
        assert {field.field_type for field in fields} == set(FormFieldType)
        assert fields_by_id["work_mode"].label == "Work arrangement"
        assert fields_by_id["work_mode"].options == ("Remote", "Hybrid")
        assert sum(field.field_id == "work_mode" for field in fields) == 1
        assert fields_by_id["employee-code"].constraints.min_length == 3
        assert fields_by_id["employee-code"].constraints.max_length == 8
        assert fields_by_id["employee-code"].constraints.pattern == "[A-Z0-9]+"
        assert fields_by_id["experience"].constraints.minimum == "0"
        assert fields_by_id["experience"].constraints.maximum == "50"
        assert fields_by_id["experience"].constraints.step == "0.5"
        assert fields_by_id["resume"].constraints.accepted_file_types == (".pdf", ".docx")
        assert fields_by_id["resume"].constraints.allows_multiple is False
        assert "disabled-field" not in fields_by_id
        assert "submit-control" not in fields_by_id

        async with PlaywrightEngine(artifact_directory=tmp_path / "fill") as fill_engine:
            fill_page = await fill_engine.new_page()
            assert (
                await fill_engine.navigate(fill_page, fixture_path.resolve().as_uri())
            ).is_successful
            invalid_fill = await fill_engine.fill_discovered_field(
                fill_page, fields_by_id["employee-code"], "lowercase"
            )
            assert invalid_fill.is_successful is False
            assert invalid_fill.error_category is not None
            assert await fill_page.get_by_label("Employee code").input_value() == ""

            valid_fill = await fill_engine.fill_discovered_field(
                fill_page, fields_by_id["location"], "Remote"
            )
            assert valid_fill.is_successful
            assert await fill_page.get_by_label("Location").input_value() == "Remote"

    asyncio.run(run_discovery())
